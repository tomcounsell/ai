---
status: Ready
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
- `55ad9ac89` "Stale-branch sweep reaps nightly-triage worktrees and sees checked-out branches" (Closes #3162) — **relevant, and it strengthens this plan rather than disturbing it.** It touches `agent/worktree_manager.py` (+102, the file spike-3 cites at line 20), `docs/features/nightly-triage-dispatch.md` (+26, a new "Lane reaping" section), and `docs/features/nightly-regression-tests.md` (+1) — all three named in this plan's Documentation tasks. Its reaper refuses to reap a lane whose `git status --porcelain` is non-empty, so a ledger written **inside** `.worktrees/{slug}/` would leave the tree permanently dirty and permanently unreapable, reintroducing exactly the worktree accumulation #3162 just fixed. That is independent corroboration for spike-3 and for Open Question 1: the ledger belongs in `data/`. **Documentation impact:** the quoted bullet in the Documentation section was read against the pre-`55ad9ac89` text; Task 5 must re-read `docs/features/nightly-triage-dispatch.md` at its current head before editing, since the file gained a section since.

**Active plans in `docs/plans/` overlapping this area:** none. No plan doc references the nightly detector; the most recent plan touching adjacent ground is `sdlc-control-plane-asserted-facts.md` (2026-09-04), which is unrelated.

**Notes:** The branch `session/nightly-triage-idempotency-3075` **exists locally only** — `git ls-remote --heads origin 'refs/heads/session/nightly-triage-idempotency-3075'` returns nothing, so it was deleted from origin after PR #3142 merged; the local ref survives because it is checked out in a worktree. It is **not** an ancestor of main, but its four commits are on main by content, squash-merged as `97354ce1e` via PR #3142. It is a leftover lane, not pending work. **The build must branch from `main`, never from that branch**, or it will reintroduce a duplicate of already-landed code. The hazard is now smaller than at critique time (a `git checkout -b ... origin/session/...` cannot resolve), but a local `git checkout` of the stale ref still can, so the Rabbit Hole and the `merge-base` Verification row both stay.

**Line-number caveat (applies to every file:line citation in this plan):** this module drifts constantly — the per-node dispatch call site was cited as line 2664 at drafting and is line 2668 on `origin/main` today, and four of this plan's recon citations drifted between recon and drafting. **Follow every citation below by symbol, not by line.** The Verification rows use `inspect.getsource` for precisely this reason and are the only line-independent instrument here.

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
- **Interface changes**: `maybe_dispatch_triage_session` gains two optional keyword arguments — `dispositions` (the script's resolved findings) and `ledger_path` is *derived* inside it rather than passed. `_build_triage_prompt` gains `dispositions` and `ledger_path`, both keyword-only and both defaulting to `None`. A new `_build_seed_prompt(seed_title, seeded_nodes)` extracts the inline baseline-seed prompt out of `main()` so all three prompts are named, testable functions. All are module-private in practice (`_build_triage_prompt` / `_build_cascade_prompt` / `_build_seed_prompt` by name; `maybe_dispatch_triage_session` by having no importer outside this module and its test file).
- **Callers of `maybe_dispatch_triage_session`: three, not two.** `dispatch_findings` calls it twice — once per cascade with `prompt=_build_cascade_prompt(cascade)` (~line 2598) and once for the surviving `single_nodes` (~line 2668) — and `main()` calls it a third time for the baseline seed with `prompt=<inline seed text>` and `slug_suffix="baseline"` (~line 2934). All three dispatch a session that files GitHub issues, and **all three currently instruct the agent to *search*.** The earlier draft of this plan said "exactly two callers" and hardened only the per-node prompt; that was wrong and is corrected throughout — see Solution, which now scopes fix 1 and fix 3 to all three and fix 2 to the two that pre-resolve.
- **Coupling**: decreases the agent's coupling to GitHub's search index, which is the point. Slightly increases coupling between `dispatch_findings` and the prompt builder — deliberate, and the direction the module has been moving since #2559 pinned literal titles for exactly this reason: the pre-flight check and the agent's instructions must not be able to drift.
- **Data ownership**: introduces one new piece of state, `data/nightly-triage-ledger/{slug}.json`, owned by the nightly script (which seeds it) and appended to by the triage agent. It is gitignored, machine-local, and advisory — losing it degrades to today's behavior rather than breaking anything.
- **Reversibility**: high, and made structurally true rather than merely asserted. Fixes 1 and 2 are text and keyword arguments in one file. Fix 3 adds one small write and one conditional prompt paragraph. The ledger paragraph is emitted **only when `ledger_path` is non-`None`**, and `ledger_path` is produced by `write_triage_ledger` returning a path on success and `None` on failure or on an empty entry list. Reverting fix 3 therefore means deleting `write_triage_ledger` and stopping the argument from being passed — the paragraph disappears on its own, with no edit to any prompt builder. Without that threading the claim would be false: a bare revert of fix 3 would leave three prompts directing the agent to read and append to a file nothing creates, which is worse than never mentioning it.

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
| Repo venv on the pinned interpreter | `python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); v='.'.join(map(str,sys.version_info[:2])); assert v==pin or pin.startswith(v)"` | `scripts/pytest-clean.sh` aborts on an off-pin venv. Scoped to the invoking venv deliberately: `python -m tools.doctor` reports the whole machine and fails on unrelated state (a stale sibling worktree, a stopped bridge, free disk), none of which blocks this work. |

## Solution


### Key Elements

- **A disposition record per node** — a small dataclass (`NodeDisposition`) carrying the node id, its computed title, what the script resolved (`file`), the REST read it resolved against, and the timestamp of that read. Built in `dispatch_findings` from state it already holds, passed through `maybe_dispatch_triage_session` to `_build_triage_prompt`.
- **A prompt that hands over a command, not an instruction** — the literal `gh issue list --state all --json number,title,state,stateReason --limit 200` line, with an explicit "do not use `gh search`, `gh issue list --search`, or the search API; that index lags issue creation by minutes and is how #2960–#2999 happened" and an explicit note that `stateReason` is `""` for open issues.
- **A pre-seeded session ledger** — `data/nightly-triage-ledger/{slug}.json`, written by the script *before* the session is dispatched, holding the dispositions and an empty `filed` list. The prompt tells the agent to read it first every turn, append `{number, title, node}` to `filed` immediately after each `gh issue create`, and treat any node already present in `filed` as done.

### Flow

Nightly run confirms failures → `dispatch_findings` reads live open+closed REST state → partitions and comments on everything already tracked → **writes `data/nightly-triage-ledger/{slug}.json` with the surviving nodes' dispositions** → dispatches one Eng session with a prompt that names the ledger path and the exact REST command → agent reads ledger → for each node not in `filed`: runs the one REST read, confirms no exact-title match, opens the issue, **appends to `filed` before moving to the next node** → turn replayed → agent reads the same ledger → every node is in `filed` → files nothing.

### Technical Approach

**Fix 1 — the prompt reads live state.** Replace the "search ALL issues" wording in `_build_triage_prompt` (line 1470) with the literal command. The read is **one call for the whole node list**, not one per node; the agent filters the returned JSON on each exact title locally. Keep the existing open/closed/`NOT_PLANNED`/`COMPLETED` decision prose verbatim — #3075 got that right and it must stay aligned with `partition_closed_matches`. Add the `stateReason == ""` warning for open rows (see Research). The prohibition on `--search` must be stated as a prohibition, not merely as a preference, and must carry its reason, so a future editor cannot soften it back.

**Fix 2 — the script's decisions cross the boundary.** In `dispatch_findings`, after the `partition_already_open` and `partition_closed_matches` calls, the surviving `single_nodes` are known to have no issue in any state. Build one `NodeDisposition` per surviving node and pass the list through the new keyword argument. The prompt then leads with, per node, the script's own finding: *"the detector read all open and closed issues at `{read_at}` and found no issue titled `{title}`; your read below is a second check against issues created since."* This does two things: it makes the agent's own read a confirmation rather than a derivation, and — because the same list is written to the ledger — a replay inherits the script's decision instead of re-deriving it.

Deliberately **not** done: passing the already-open and closed-not-planned nodes through. The script comments on those itself and drops them before this point; handing them to the agent would create a second writer for the same comment. The channel carries only nodes the agent is being asked to act on.

**Fix 3 — the ledger.** `data/nightly-triage-ledger/{slug}.json`, written by a new `write_triage_ledger(slug, dispositions)` helper in the same module, using the same `DATA_DIR.mkdir(parents=True, exist_ok=True)` idiom `save_last_run` already uses (line 467). Shape:

```json
{
  "slug": "nightly-triage-a1b2c3d4",
  "created_at": "2026-09-05T06:00:00Z",
  "dispositions": [
    {"node": "tests/unit/test_a.py::test_1",
     "title": "Nightly regression: tests/unit/test_a.py::test_1",
     "disposition": "file",
     "resolved_against": "gh issue list --state all (open+closed REST read)",
     "resolved_at": "2026-09-05T06:00:00Z"}
  ],
  "filed": []
}
```

Written **before** `subprocess.run(... valor_session create ...)`, so it exists the instant the session can start. Absolute path interpolated into the prompt — the agent runs from its lane worktree and a relative path would resolve wrong.

The write is best-effort and logged on failure, matching how the module treats every other side effect: a ledger that cannot be written must not stop the night from filing. That is the same fail-open posture `open_issues()` takes (`None` means "could not tell", dispatch proceeds), and for the same reason — a missing defense is a smaller harm than a silent night during a real regression.

**Dry-run.** `maybe_dispatch_triage_session` short-circuits on `dry_run` before the subprocess (line 2340, returning `DRY_RUN_SESSION_ID` at 2345). The ledger write must sit **after** that short-circuit, or `--dry-run` starts writing state files. This is exactly the bug the dry-run sentinel was introduced to fix (the docstring at line 2316 records it) and the plan must not reintroduce it.

## Failure Path Test Strategy


### Exception Handling Coverage

- [ ] `write_triage_ledger` is the one new function that can fail (disk full, permissions, a `data/` that is somehow a file). It must catch broadly, `log()` a `WARNING`, and return `False` — never raise into the dispatch path. Test asserts the observable: dispatch still proceeds and a warning line reaches `LOG_FILE`.
- [ ] Existing handlers in scope are unchanged: `open_issues` (lines 1970 and 1987), `closed_issue_dispositions` (lines 2033 and 2078), `maybe_dispatch_triage_session` (lines 2367 and 2373) each already catch broadly and `log()` — all three have `test_open_issues_returns_none_on_any_failure`-style coverage. No `except Exception: pass` exists in this module; every handler logs.

### Empty/Invalid Input Handling

- [ ] `_build_triage_prompt([])` — currently unreachable (`maybe_dispatch_triage_session` returns `None` on an empty list first, line 2329, covered by `test_...([]) is None`). The new keyword argument must not change that: passing an empty disposition list alongside a non-empty node list must degrade to the old prompt rather than emitting a malformed pre-resolved block.
- [ ] `write_triage_ledger(slug, [])` — must write nothing and return `False` rather than creating an empty ledger a replay would read as "nothing to file".
- [ ] Disposition list and node list of **differing length** — a defect that would silently mislabel nodes. The prompt builder must zip them `strict=True` (matching line 1482's existing use) so a mismatch raises at build time rather than producing a prompt attributing one node's disposition to another.

### Error State Rendering

- [ ] The night's user-visible output is the GitHub issue and `logs/nightly_tests.log`. A failed ledger write must appear in the log with the slug named, not be swallowed — the operator's only signal that the replay defense is degraded for that dispatch.
- [ ] `--dry-run` must print what it *would* write and write nothing; assert no file appears under `data/nightly-triage-ledger/`.

## Test Impact


`tests/unit/test_nightly_regression_tests.py` (2835 lines) is the only test file touching this code. The changes are additive at every seam, so almost nothing breaks — but three existing tests sit directly on the surfaces being changed and must be strengthened rather than left to pass vacuously.

- [ ] `tests/unit/test_nightly_regression_tests.py::TestBuildTriagePrompt::test_literal_titles_present` (line 796) — **UPDATE**. Asserts only that each `Nightly regression: {node}` title appears in the prompt. Still true after the change and therefore blind to it. Add sibling assertions: the literal `gh issue list --state all` substring is present, `--search` is absent, and `gh search` is absent.
- [ ] `tests/unit/test_nightly_regression_tests.py::test_open_issues_uses_the_rest_list_not_the_lagging_search` (line 960) — **UPDATE**: no change to the assertions, but this is the pattern the new prompt guard mirrors, and the new test should reference it by name in its docstring so the two read as one contract.
- [ ] The `maybe_dispatch_triage_session` call-shape tests (lines 1174, 1223, 1279, 1300, 1332, 1353, 1386, 1401, 1564) — **UPDATE where signature-sensitive**. Most stub with `lambda *a, **k` or `lambda ns, **kw` and absorb a new keyword argument without change. `lambda ns, **kw: "sess-1"` (lines 1353, 1401) also survives, since the new argument is keyword-only. Audit each of the nine, confirm which actually bind positionally, and change only those. Do not blanket-rewrite working stubs.
- [ ] `tests/unit/test_nightly_regression_tests.py::TestMaybeDispatchTriageSession` dry-run cases (lines 1431, 1489–1513) — **UPDATE**: add an assertion that no ledger file appears under the patched `data/nightly-triage-ledger/` on a dry run. Without this the dry-run/ledger ordering (Solution, "Dry-run") is unguarded.

New coverage to add (not modifications):

- [ ] `TestBuildTriagePrompt` — pre-resolved dispositions render per node; `zip(..., strict=True)` raises on a length mismatch; an empty disposition list degrades to the plain prompt.
- [ ] `TestWriteTriageLedger` — happy path shape; empty dispositions writes nothing and returns `False`; an unwritable path logs a `WARNING` and returns `False` without raising.
- [ ] `TestDispatchFindings` — the dispositions handed to `maybe_dispatch_triage_session` cover exactly the surviving `single_nodes`, and contain **no** already-open or closed-not-planned node (the second writer hazard from Solution, fix 2).

## Rabbit Holes


- **Diagnosing why the turn was replayed.** The `_agent_session_health_check` requeue legs, the `tool_timeout` path, the role driver's stale-UUID fallback — all of it is #3161's, all of it needs the nightly host this machine is not, and none of it is a prerequisite for making filing idempotent. If the replay mechanism is fixed tomorrow these three defenses are still correct.
- **Building a general-purpose agent-side idempotency framework.** A `filed_issues` ledger abstraction for every dispatching skill is a tempting generalization of fix 3 and would swallow the appetite whole. One JSON file, one writer, one reader.
- **Branching from `session/nightly-triage-idempotency-3075`.** Its name matches this work and it is not merged, which makes it look like the natural base. Its content is already on main via PR #3142's squash. Branching there duplicates landed code and produces a diff nobody can review. **Branch from `main`.**
- **Rewriting the open/closed decision prose in the prompt.** #3075 tuned that language against `partition_closed_matches` and `closed_epilogue`. Fix 1 changes the *read mechanism*, not the *decision rule*. Rewording the rule risks drift between the prompt and the pre-flight — the exact failure #2559 pinned literal titles to prevent.
- **Chasing the two-simultaneous-filers shape** (#2971, #2982–#2989 interleaving the 20:40 wave). That is a second machine, not a replay; `open_issues()` already reads live REST state and sees another machine's issues instantly. Different problem, no evidence it is currently broken.
- **Tuning `--limit 200`.** Picking the perfect window is a research project with a wrong answer at every repo size. Take the issue's number, state the failure mode, and move on — see Risks.

## Risks


### Risk 1: `--limit 200` silently under-reads on a busy repo

**Impact:** The agent's confirmation read is a newest-created-first window. At ~1900 closed issues in this repo, 200 rows covers only recent history. A node whose issue was filed and closed long ago falls outside the window, the agent sees no match, and re-files — reintroducing the #3075 defect at the agent layer while the script's own read (`CLOSED_ISSUE_LIST_LIMIT = 4000`) still gets it right.

**Mitigation:** The script's pre-resolution is the primary defense and it uses the wide window; the agent's read is explicitly framed in the prompt as a *second check against issues created since the script's read*, not as the authority. Reordering the risk this way is why fix 2 must land with fix 1 rather than after it. The prompt states the window's purpose so the agent does not over-trust it, and states that the script already checked the full closed set.

### Risk 2: The ledger is consulted but never written, or written but never consulted

**Impact:** A defense that exists in the prompt and not in behavior is worse than no defense — it invites the next investigator to conclude the hole is covered.

**Mitigation:** Two separate guards, one on each half. A test asserts `write_triage_ledger` produces the file with the seeded dispositions before dispatch; a second asserts the prompt contains the ledger's absolute path and the append-before-next-node instruction. Neither test can pass on the other's work.

### Risk 3: The prompt regresses to "search" in a later edit

**Impact:** This is the fourth pass at this bug and prompt wording has drifted before — `8524e765b` hardened the script and left the prompt saying "search", which is precisely how we got here.

**Mitigation:** A verification anti-criterion greps the module's prompt-building region for `--search` and `gh search` and requires zero matches, so a reintroduction fails the gate rather than reaching a nightly run. The prohibition in the prompt text carries its reason inline (#2960–#2999) so a future editor sees the cost before softening it.

### Risk 4: The ledger write breaks `--dry-run`

**Impact:** `--dry-run` is the only safe way to preview a night. The dry-run sentinel exists (docstring, line 2316) because an earlier version spawned real sessions that filed real issues under `--dry-run`. A ledger write placed before the short-circuit puts state-file writes back into the preview path.

**Mitigation:** Ordering is specified in Solution and guarded by a dry-run test asserting no file appears under `data/nightly-triage-ledger/`.

### Risk 5: Stale ledgers accumulate in `data/`

**Impact:** One file per dispatch slug, unbounded. Low severity — small JSON, gitignored, machine-local — but unbounded growth is how `data/` directories become a problem years later.

**Mitigation:** Slugs are a hash of the node set, so a recurring failure set reuses its slug and overwrites rather than accumulating. Growth is bounded by the number of *distinct* failure sets, not by nights. No pruning job for now; noted here so a future reader knows it was considered rather than missed.

## Race Conditions


### Race 1: Two triage sessions on one slug appending to one ledger

**Location:** `data/nightly-triage-ledger/{slug}.json`; writers are `write_triage_ledger` (script, seed) and the triage agent (appends to `filed`).

**Trigger:** The 2026-08-24 evidence shows two filers live at once (#2971 / #2982–#2989 interleaving the 20:40 wave). If two sessions ever resolve to the same slug, both read-modify-write the same JSON and a lost update drops one session's `filed` entries — the ledger then under-reports and a replay re-files.

**Data prerequisite:** The ledger must exist and hold the seeded dispositions before any session starts appending.

**State prerequisite:** One writer per slug at a time.

**Mitigation:** The slug is `sha256` of the sorted node set, so two sessions share a slug only when they were dispatched for an identical node set — and `compute_dispatch_set` plus the run lock (`_acquire_run_lock`, `data/nightly_tests.lock`, taken as the first act of `main()`) make that near-impossible within a machine. Across machines the ledger is machine-local and the two never share a file. A lost update degrades the ledger to partial, which falls back to fixes 1 and 2, which is today's behavior plus improvements. **Explicitly not adding file locking** — the cost is not justified for a third-line advisory defense, and this reasoning belongs in the code comment so a reviewer does not read the omission as an oversight.

### Race 2: An issue created between the script's read and the agent's read

**Location:** `dispatch_findings` (`open_issues()` / `closed_issue_dispositions()` at lines 2523–2529) versus the agent's `gh issue list` seconds-to-minutes later.

**Trigger:** Another machine's nightly, or a human, files the exact title in the gap.

**Data prerequisite:** none.

**State prerequisite:** none.

**Mitigation:** This is the ordinary case the agent's own read exists to catch, and it works *because* the read is REST rather than search — a search-index read cannot see an issue created seconds ago, which is the entire defect. Fix 1 is the mitigation. The residual window (an issue created between the agent's read and its `gh issue create`) is sub-second and out of appetite.

### Race 3: Ledger seeded, session dispatch then fails

**Location:** `maybe_dispatch_triage_session`, between the ledger write and the `subprocess.run` returning non-zero or raising (lines 2367, 2373).

**Trigger:** `valor_session create` fails; the ledger exists with dispositions and an empty `filed`.

**Data prerequisite:** none.

**State prerequisite:** The next run must retry these nodes, not treat them as handled.

**Mitigation:** No new hazard. `carry_dispatched_nodes` already records only what `DispatchOutcome.recorded` names, and a failed dispatch returns `None` so its nodes are never recorded (guarded by `test_failed_dispatch_records_nothing`, line 788). The orphan ledger is harmless: `filed` is empty, so a later session on the same slug reads it, sees nothing filed, and proceeds. The ledger must therefore be keyed on the slug and never treated as proof that filing occurred — only its `filed` array carries that meaning.

## No-Gos (Out of Scope)


- [SEPARATE-SLUG #3161] **Diagnosing the replay mechanism itself** — reading `logs/worker.log`, `valor-session telemetry`, and `session_events` for `0_1787603653699` to establish which requeue leg produced three fresh contexts at ~300s spacing. Requires the machine that ran `com.valor.nightly-tests` on 2026-08-24; this one is worker-only with `data/nightly-tests-disabled` present, and `valor-session inspect --id 0_1787603653699` reports not found. #3161 is open and holds this.
- [SEPARATE-SLUG #3161] **Preventing two triage filers from running simultaneously** — the #2971 / #2982–#2989 interleave is a second live filer with a different node list from a different working tree, not a replay. Cross-machine dedup already rests on live REST reads and no evidence says it is currently failing. Belongs with the same investigation.
- [EXTERNAL] **Verifying the fix against a real nightly run** — the nightly is disabled on this machine (`data/nightly-tests-disabled`) and enabling it on the host that runs `com.valor.nightly-tests` is an operator action on a machine the agent cannot reach. Unit coverage plus `--dry-run` is what this plan can establish; the first real confirmation is the next night on that host.

## Update System


No update system changes required. The change is three edits inside `scripts/nightly_regression_tests.py` plus its tests and one doc — no new dependency, no new config file, no new env key, no plist change, and no schema. `scripts/remote-update.sh` propagates the repo by pulling; the new code arrives with it.

One thing worth stating because it is easy to assume otherwise: **no service restart is needed.** The nightly detector is not a resident process. `com.valor.nightly-tests` invokes `scripts/nightly_regression_tests.py` fresh on each schedule fire, so the next night after the pull runs the new code. The bridge and worker do not import this module — verified: its only importer is `tests/unit/test_nightly_regression_tests.py`, via `sys.path` insertion.

`data/nightly-triage-ledger/` is created on demand by `write_triage_ledger` using the same `mkdir(parents=True, exist_ok=True)` idiom `save_last_run` uses (line 467). No migration step, no pre-created directory on any machine.

## Agent Integration


No new agent-facing surface is required — but this work *is* agent integration in the plain sense, and the wiring already exists in a form worth naming precisely, because "add a CLI entry point" would be the wrong instinct here.

The triage agent is reached through exactly one channel: `maybe_dispatch_triage_session` shells out to `tools.valor_session create --role eng --slug ... --message <prompt>` (line 2347 onward). **The prompt string is the entire interface.** Everything this plan gives the agent — the REST command, the pre-resolved dispositions, the ledger path — travels as prompt text through that one argument. There is nothing to register in `pyproject.toml [project.scripts]`, nothing to add to `.mcp.json`, and no new import for `bridge/telegram_bridge.py`.

The agent already has the two capabilities it needs: `Bash` (to run `gh issue list` and `gh issue create`) and `Read`/`Write` (for the ledger). Both are standard for an `eng` role session. No permission change.

Integration coverage: the existing tests assert the dispatch subprocess argv shape (`TestMaybeDispatchTriageSession`, lines 1489–1513). New assertions extend that to the message content — that the argv's `--message` value carries the ledger's absolute path and the literal `gh issue list --state all`. That is the honest integration test available here; end-to-end confirmation that a live agent obeys the prompt requires the nightly host and is recorded as an `[EXTERNAL]` No-Go.

## Documentation


### Feature Documentation

- [ ] Update `docs/features/nightly-triage-dispatch.md` — its "Triage dispatch" bullet (in "What It Does") currently says the prompt "states the same open-and-closed rule so the pre-flight and the agent's instructions cannot drift". Extend that to the three defenses: the prompt hands over the literal REST command rather than an instruction to search, the script passes its resolved dispositions across the dispatch boundary, and a session-local ledger records what was filed. Name the #2960–#2999 wave as the incident that motivated it, as the doc already does for #3131 and #3134.
- [ ] Add a "Replay Idempotency" subsection to the same doc documenting `data/nightly-triage-ledger/{slug}.json`: its path, its shape, who writes each field, that it is advisory and fail-open, and that it is deliberately unlocked (Race 1).
- [ ] Check `docs/features/nightly-regression-tests.md` for statements about the triage prompt's dedup contract and update any that describe the search-based read. The two docs cross-reference each other and must not disagree.
- [ ] `docs/features/README.md` — no new row needed; both affected docs are already indexed. Confirm this rather than assume it.

### External Documentation Site

Not applicable — this repo has no Sphinx/MkDocs/Read the Docs site.

### Inline Documentation

- [ ] `_build_triage_prompt` docstring: state that the prompt hands over a REST command and why `--search` is prohibited, citing #2960–#2999 and `8524e765b`. The rationale must live next to the code so a future editor meets it before softening the wording.
- [ ] `write_triage_ledger` docstring: the fail-open posture, the advisory status, and the deliberate absence of file locking (Race 1) — an unexplained missing lock reads as an oversight to a reviewer.
- [ ] `maybe_dispatch_triage_session` docstring: document the new keyword argument and the ordering constraint that the ledger write follows the `dry_run` short-circuit (line 2340), citing the earlier dry-run defect the sentinel was introduced for.

## Success Criteria


- [ ] `_build_triage_prompt` emits the literal `gh issue list --state all --json number,title,state,stateReason --limit 200` and no longer contains `search ALL`, `--search`, or `gh search`.
- [ ] The prompt warns that `stateReason` is `""` for open issues, so the agent branches on `state` first (Research finding).
- [ ] The prompt's open / closed-`NOT_PLANNED` / closed-`COMPLETED` decision rule is unchanged in meaning from what #3075 landed — the read mechanism changed, the rule did not.
- [ ] `dispatch_findings` passes per-node dispositions for exactly the surviving `single_nodes` into `maybe_dispatch_triage_session`, and that list contains no already-open and no closed-not-planned node.
- [ ] `write_triage_ledger` writes `data/nightly-triage-ledger/{slug}.json` with the seeded dispositions and an empty `filed` array, **before** the session subprocess starts and **after** the `dry_run` short-circuit.
- [ ] A `--dry-run` invocation creates no file under `data/nightly-triage-ledger/`.
- [ ] The prompt names the ledger's absolute path and instructs the agent to read it first each turn and append to `filed` immediately after each `gh issue create`, before moving to the next node.
- [ ] A ledger write failure logs a `WARNING` naming the slug and does not prevent dispatch.
- [ ] `_build_triage_prompt` raises on a node/disposition length mismatch rather than mis-attributing a disposition (`zip(..., strict=True)`).
- [ ] Tests pass (`/do-test`) — `./scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py -q` exits 0.
- [ ] Documentation updated (`/do-docs`) — `docs/features/nightly-triage-dispatch.md` describes all three defenses and the ledger's shape.
- [ ] `python -m ruff check` and `python -m ruff format --check` clean on the changed files.
- [ ] The branch is rooted on `main`, not on `session/nightly-triage-idempotency-3075` (`git merge-base --is-ancestor origin/main HEAD`).

## Team Orchestration


When this plan is executed, the lead agent orchestrates work using Task tools. The lead never builds directly.

Small appetite, one source file: the split is by *concern*, not by file, and the two builders must not both edit `scripts/nightly_regression_tests.py` at once. **`prompt-builder` owns `_build_triage_prompt` and `dispatch-builder` owns `write_triage_ledger`, `maybe_dispatch_triage_session`, and `dispatch_findings`** — a declared function-level ownership split, and they run sequentially rather than in parallel because they share a file. This is deliberate: shared-file builders converging on each other's edits is a known livelock here.

### Team Members

- **Builder (prompt)**
  - Name: `prompt-builder`
  - Role: Fix 1 only — rewrite `_build_triage_prompt` to hand over the REST command, accept the disposition keyword argument, and render the pre-resolved block. Owns nothing else in the file.
  - Agent Type: builder
  - Resume: true

- **Builder (dispatch + ledger)**
  - Name: `dispatch-builder`
  - Role: Fixes 2 and 3 — `write_triage_ledger`, the disposition dataclass, the `dispatch_findings` handoff, and the ordering of the ledger write against the `dry_run` short-circuit.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `nightly-test-engineer`
  - Role: All new coverage and the Test Impact updates. Must mutation-check each new guard: break the behavior, confirm the test goes red, restore.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `nightly-documentarian`
  - Role: The four Documentation tasks.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `nightly-validator`
  - Role: Read-only. Runs every Verification row and reports pass/fail per row with the command output.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard Tier 1 roster. No domain framing needed — this is ordinary Python with a subprocess boundary, not async, Redis, or untrusted-input work.

## Step by Step Tasks


### 1. Ledger, dispositions, and the dispatch handoff
- **Task ID**: build-dispatch
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_regression_tests.py`
- **Informed By**: spike-2 (every node reaching `maybe_dispatch_triage_session` is already known to have no issue in any state), spike-3 (ledger belongs in `data/`, not the lane worktree)
- **Assigned To**: `dispatch-builder`
- **Agent Type**: builder
- **Parallel**: false
- Branch from `main`. Confirm with `git merge-base --is-ancestor origin/main HEAD` before the first commit — **not** from `session/nightly-triage-idempotency-3075`.
- Add a `NodeDisposition` dataclass: `node`, `title`, `disposition`, `resolved_against`, `resolved_at`.
- Add `write_triage_ledger(slug, dispositions) -> bool` writing `data/nightly-triage-ledger/{slug}.json` with `slug`, `created_at`, `dispositions`, and an empty `filed`. Empty dispositions writes nothing and returns `False`. Catch broadly, `log()` a `WARNING` naming the slug, return `False`; never raise.
- Add a keyword-only `dispositions` argument to `maybe_dispatch_triage_session`, defaulting to `None`. Call `write_triage_ledger` **after** the `if dry_run:` short-circuit (line 2340) and **before** the `subprocess.run` that creates the session. Pass `dispositions` through to `_build_triage_prompt`.
- In `dispatch_findings`, build the disposition list from the surviving `single_nodes` after `partition_already_open` and `partition_closed_matches`, and after the issue-budget truncation, so it matches the nodes actually dispatched. Pass it at the call site (line 2664).
- Docstring the fail-open posture, the deliberate absence of file locking (Race 1), and the dry-run ordering constraint.
- Commit with explicit paths as soon as the code is coherent; do not hold the file open across the next task.

### 2. The prompt hands over a command
- **Task ID**: build-prompt
- **Depends On**: build-dispatch
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestBuildTriagePrompt`
- **Informed By**: spike-1 (`--state all` returns all four fields, ~1s; `stateReason` is `""` for open issues)
- **Assigned To**: `prompt-builder`
- **Agent Type**: builder
- **Parallel**: false
- Pull `build-dispatch`'s commit first. Edit only `_build_triage_prompt`.
- Replace "search ALL issues — open AND closed — for the EXACT title given" with the literal `gh issue list --state all --json number,title,state,stateReason --limit 200`, run **once for the whole node list** and filtered locally per exact title.
- Add the prohibition explicitly: do not use `gh search`, `gh issue list --search`, or the search API; that index lags issue creation by minutes and is how #2960–#2999 happened.
- Add the `stateReason` note: it is `""` for open issues, so branch on `state` first.
- Keep the open / `NOT_PLANNED` / `COMPLETED` decision prose unchanged in meaning.
- Render the pre-resolved block per node when `dispositions` is provided, zipped `strict=True` against the node list; degrade to the plain prompt when it is `None` or empty.
- Append the ledger paragraph: the absolute path, read it first every turn, skip any node already in `filed`, append `{number, title, node}` immediately after each `gh issue create` and before the next node.
- Docstring the REST-over-search rationale citing #2960–#2999 and `8524e765b`.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-prompt
- **Validates**: `tests/unit/test_nightly_regression_tests.py`
- **Assigned To**: `nightly-test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Apply every Test Impact disposition, including the nine-stub audit — change only stubs that actually bind positionally.
- Add `TestWriteTriageLedger`, extend `TestBuildTriagePrompt`, extend the `TestDispatchFindings` and dry-run cases per Test Impact.
- **Mutation-check each new guard individually**: break the behavior it claims to protect, confirm that specific test goes red, restore, re-measure. A guard that stays green under its own mutation reaches no code and must be rewritten, not kept.
- Record the mutation results in the PR description.

### 4. Validate build and tests
- **Task ID**: validate-code
- **Depends On**: build-tests
- **Assigned To**: `nightly-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `./scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py -q`, `python -m ruff check`, `python -m ruff format --check`.
- Run the structural Verification rows and report each with its output.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-code
- **Assigned To**: `nightly-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the four Documentation tasks.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-dispatch, build-prompt, build-tests, validate-code, document-feature
- **Assigned To**: `nightly-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and every Success Criterion; report pass/fail with evidence per line.

## Verification


Structural rows use `inspect.getsource` rather than line-scoped `sed`, so they survive the line drift this module sees constantly (four of this plan's own citations drifted between recon and drafting). Every command below was executed against `feebe32aa` while drafting; the "Red today" column is the measured pre-fix result, which is the red-state proof that these rows bite.

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `./scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/nightly_regression_tests.py tests/unit/test_nightly_regression_tests.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/nightly_regression_tests.py tests/unit/test_nightly_regression_tests.py` | exit code 0 |
| Prompt hands over the REST command | `python -c "import sys; sys.path.insert(0,'scripts'); import inspect, nightly_regression_tests as n; print(inspect.getsource(n._build_triage_prompt).count('gh issue list --state all'))"` | output > 0 |
| Prompt never names the lagging search (anti-criterion) | `python -c "import sys; sys.path.insert(0,'scripts'); import inspect, nightly_regression_tests as n; s=inspect.getsource(n._build_triage_prompt); print(s.count('--search')+s.count('gh search')+s.count('search ALL'))"` | match count == 0 |
| Prompt warns about the open-issue `stateReason` | `python -c "import sys; sys.path.insert(0,'scripts'); import inspect, nightly_regression_tests as n; print(inspect.getsource(n._build_triage_prompt).count('stateReason'))"` | output > 0 |
| Ledger helper exists | `python -c "import sys; sys.path.insert(0,'scripts'); import nightly_regression_tests as n; print(int(callable(n.write_triage_ledger)))"` | output contains 1 |
| Ledger write follows the dry-run short-circuit (anti-criterion) | `python -c "import sys; sys.path.insert(0,'scripts'); import inspect, nightly_regression_tests as n; s=inspect.getsource(n.maybe_dispatch_triage_session); print(int(s.index('write_triage_ledger') < s.index('return DRY_RUN_SESSION_ID')))"` | match count == 0 |
| Dispositions cross the dispatch boundary | `python -c "import sys; sys.path.insert(0,'scripts'); import inspect, nightly_regression_tests as n; print(inspect.getsource(n.dispatch_findings).count('dispositions'))"` | output > 0 |
| Named ledger tests exist and pass | `./scripts/pytest-clean.sh tests/unit/test_nightly_regression_tests.py -q -k "TestWriteTriageLedger or TestBuildTriagePrompt"` | exit code 0 |
| Branch is rooted on main (not the stale #3075 lane) | `git merge-base --is-ancestor origin/main HEAD; echo $?` | output contains 0 |

**Red-state proof measured at `feebe32aa` before any change:**

| Row | Pre-fix result | Verdict |
|-----|----------------|---------|
| Prompt hands over the REST command | `0` | FAIL (needs > 0) — row bites |
| Prompt never names the lagging search | `1` (from `search ALL`) | FAIL (needs 0) — anti-criterion bites |
| Prompt warns about `stateReason` | `0` | FAIL (needs > 0) — row bites |
| Ledger helper exists | `AttributeError` | FAIL — row bites |

Each of the four structural rows fails against current `main`, so none of them can pass vacuously. The `git merge-base` row was verified to exit 0 on a `main`-rooted checkout and non-zero on `session/nightly-triage-idempotency-3075`.

## Critique Results

War room, FULL depth (Risk & Robustness, Scope & Value, History & Consistency) plus automated structural checks. Verdict: **NEEDS REVISION** (2 blockers).

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Structural | The plan mandates text that its own gate forbids. Solution fix 1 and Task 2 require `_build_triage_prompt` to carry the prohibition "do not use `gh search`, `gh issue list --search`, or the search API", and the Documentation section requires the same function's docstring to state "why `--search` is prohibited". The Verification anti-criterion row and Success Criterion 1 both require `inspect.getsource(_build_triage_prompt)` to contain zero occurrences of `--search`, `gh search`, or `search ALL`. `inspect.getsource` returns the docstring and the prompt body, so implementing the plan as written guarantees the anti-criterion fails. Measured on `origin/main` today the count is 1; the plan's red-state table calls that a passing red state, but the green state it demands is unreachable. | pending | Split the check in two. Assert the RENDERED prompt is clean by calling the function: `s = n._build_triage_prompt(['x'])` and requiring `s.count('gh issue list --search') + s.count('gh search') + s.count('search ALL') == 0` only for the instruction sentences, and assert the prohibition sentence is PRESENT as a separate positive row. Do not scan `inspect.getsource` for a token the plan simultaneously requires the source to contain. Alternatively word both the docstring rationale and the prompt prohibition without the literal `--search` token ("the search index", "the search API") and keep the source-scan row. |
| BLOCKER | Scope & Value, History & Consistency, Structural | Two of the three prompts that file GitHub issues keep the exact defect. `_build_cascade_prompt` says "Search ALL issues — open AND closed — for the EXACT title below" and the inline baseline-seed prompt says "Search open AND closed issues for the EXACT title". Both dispatch through `maybe_dispatch_triage_session` via its `prompt=` override, so both are equally replay-vulnerable, and neither receives dispositions nor a ledger (the plan specifies `write_triage_ledger` writes nothing on an empty disposition list). The plan's Architectural Impact also states `maybe_dispatch_triage_session` has "exactly two callers"; there are three (the cascade site, the per-node site, and the baseline-seed site). No No-Go or Open Question acknowledges the gap, and the Verification anti-criterion scans only `_build_triage_prompt`, so the gate reports clean with the hole open. | pending | Decide explicitly, then encode the decision. If extending: apply the same REST command, prohibition, and `stateReason` note to `_build_cascade_prompt` and the seed prompt, and widen the anti-criterion to scan all three prompt sources in one row. If deferring: add a named No-Go stating the cascade and seed paths are out of scope with the reason (the 2026-08-24 wave was per-node), matching how Race 1 records the deliberate absence of file locking. Silence is the failure mode, because a future reader cannot tell a deferral from an oversight. |
| CONCERN | History & Consistency, Structural | The dry-run ordering anti-criterion cannot express what it claims. It computes `s.index('write_triage_ledger') < s.index('return DRY_RUN_SESSION_ID')` over the whole `inspect.getsource(maybe_dispatch_triage_session)` string, but the Documentation section requires that function's docstring to name the ordering constraint, and the docstring precedes the code. The first occurrence of the bare name would then be the docstring mention, flipping the row to 1 (fail) on correct code. Separately, `str.index` raises `ValueError` when the substring is absent, so the row has no defined pre-fix result. It is one of the seven Verification rows the plan's red-state proof table omits. | pending | Match the call site, not the bare name: `s.index('write_triage_ledger(')`, or strip the docstring first with `s.split('"""', 2)[-1]`. Then add the row to the red-state proof table with its measured pre-fix result, or replace the source-string check outright with a behavioral dry-run test that patches `DATA_DIR` to a tmp_path and asserts no file appears (that test is already required by Test Impact, so the structural row is redundant with it). |
| CONCERN | Structural | The Verification row "Dispositions cross the dispatch boundary" passes on unmodified `main`. It counts `dispositions` in `inspect.getsource(dispatch_findings)` and expects greater than zero; the measured value at `origin/main` today is already 1, from the existing `closed_issue_dispositions()` call inside that function. The row cannot distinguish the fix from HEAD, and it is not in the plan's red-state proof table, so nothing caught it. | pending | Count a token that only the fix introduces, e.g. `dispositions=` (the keyword at the call site), and verify the pre-fix count is 0 before writing the row down. Better: assert it behaviorally in `TestDispatchFindings` by capturing the kwargs `maybe_dispatch_triage_session` was called with, which the plan already plans to test. Every Verification row belongs in the red-state proof table with a measured value, not just the four that were checked. |
| CONCERN | Risk & Robustness | Failure Path Test Strategy specifies contradictory behavior for one input shape. One bullet says "passing an empty disposition list alongside a non-empty node list must degrade to the old prompt"; a later bullet says a node/disposition length mismatch must raise via `zip(..., strict=True)`. An empty list against a non-empty node list is a length mismatch by the second bullet's own definition, so a builder who zips first raises where the plan says degrade. | pending | State the precedence in the Solution text: `if not dispositions:` (catching both `None` and `[]`) returns the plain prompt BEFORE reaching the strict zip; only a non-empty wrong-length list reaches the zip and raises. Pin the two behaviors in separate tests — `dispositions=[]` against 3 nodes returns the plain prompt, `dispositions` of length 2 against 3 nodes raises `ValueError` — so they cannot collapse into one code path. |
| CONCERN | Risk & Robustness | The reversibility claim does not hold for fix 3. Architectural Impact says "Any of the three can be reverted independently without touching the others", but Solution's Key Elements has fix 1's prompt naming the ledger's absolute path and instructing the agent to read it first every turn. Reverting fix 3 alone leaves the prompt directing the agent to read and append to a file nothing creates, which is worse than never having mentioned it. | pending | Thread a `ledger_path: str \| None` argument from `maybe_dispatch_triage_session` into `_build_triage_prompt` and emit the ledger paragraph only when it is non-`None`. Reverting fix 3 then stops passing the argument and the paragraph disappears with no edit to `_build_triage_prompt`, which is what the reversibility claim asserts. |
| CONCERN | History & Consistency, Structural | The Freshness Check is one commit stale in an area it depends on. It lists only `35a225c19` as landing since baseline `feebe32aa`; `55ad9ac89` ("Stale-branch sweep reaps nightly-triage worktrees and sees checked-out branches", Closes #3162) also landed and touches `agent/worktree_manager.py` (the file spike-3 cites at line 20 to justify Open Question 1's ledger relocation), `docs/features/nightly-triage-dispatch.md` (+26 lines, a new "Lane reaping" section), and `docs/features/nightly-regression-tests.md` — all three named in this plan's Documentation tasks. Independently verified: `origin/session/nightly-triage-idempotency-3075` no longer exists on origin (the local ref survives, checked out in a worktree), so the Freshness Check's "exists locally and on origin" is stale and the corresponding Rabbit Hole is now partly moot. | pending | Add `55ad9ac89` to the Freshness Check with a disposition line. Its reaper is gated on `git status --porcelain` cleanliness, which strengthens spike-3 rather than undermining it (a ledger inside the lane would make the tree dirty and block reaping, reintroducing #3162's accumulation), so cite it as supporting evidence for Open Question 1. Re-read the Documentation section's quoted bullet against the new doc text before Task 5, and update the stale-branch wording to "exists locally only". |
| CONCERN | Scope & Value | Orchestration is heavier than the appetite. Five named agents and six tasks, every one `Parallel: false`, for one source file, one test file, and one doc. The stated justification (two builders must not share `scripts/nightly_regression_tests.py`) supports serializing `build-prompt` after `build-dispatch` and nothing more; it does not explain why `document-feature` waits on a full pytest and ruff run when its file set is disjoint from every builder's. | pending | Change Task 5's `Depends On` from `validate-code` to `build-prompt`. Task 6 already depends on `document-feature`, so the final gate is unchanged and only the middle of the pipeline shortens. Keep `build-dispatch` to `build-prompt` serialized, since that is the genuine shared-file constraint. |
| NIT | Structural | Line citations have drifted since the plan's baseline. The discarding call site is cited as line 2664 throughout the plan; it is at line 2668 on `origin/main` today. The plan itself observes that this module drifts constantly and that its Verification rows use `inspect.getsource` for exactly that reason, so the prose citations should carry the same caveat. | pending | No action required for the build. Locate by symbol rather than by line when following these citations. |
---

## Open Questions


1. **The ledger lives in `data/nightly-triage-ledger/{slug}.json`, not "under the lane worktree" as the issue specifies.** Reasoning in spike-3: `.worktrees/{slug}/` is a git checkout, so a ledger there pollutes the agent's own `git status`, and it is destroyed on lane teardown — a replay after teardown would find nothing. `data/` is already this script's state home, is gitignored, survives teardown, and is reachable by absolute path from inside a worktree. The slug-keyed filename preserves the "session-local" property the issue was reaching for. **Confirm this deviation is acceptable, or say the word and it moves to the worktree.**

2. **`--limit 200` for the agent's confirmation read is taken from the issue verbatim and is narrower than the script's own `CLOSED_ISSUE_LIST_LIMIT = 4000`.** Risk 1 argues this is fine *because* fix 2 makes the script's wide read the authority and the agent's read only a check for issues created since. If you would rather the agent's read match the script's window, that is a one-token change with roughly 40 REST calls of cost per dispatch instead of 2.

3. **Should a ledger write failure block the dispatch instead of logging and continuing?** The plan chooses fail-open, matching `open_issues()` returning `None` and the module's stated posture that a silent night during a real regression is the worse harm. The opposite reading — that a dispatch without its replay defense should not go out at all — is defensible given this bug has now recurred four times. Fail-open is the default unless you say otherwise.
