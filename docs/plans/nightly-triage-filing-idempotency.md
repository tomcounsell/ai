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
