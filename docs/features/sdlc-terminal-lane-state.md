# SDLC Terminal Lane State

`agent.sdlc_router.guard_terminal_lane` ("T") gives the router a clean way to
say **"this lane is finished, and there is nothing wrong"** — distinct from
`Blocked`, which says the router could not decide and a human should look.
Issues #2894 and #2817.

## The problem

Recon on 2026-08-26 measured four different dispatch-table rows each firing on
a lane that had already shipped:

- row 10 (`/do-merge`) — the originally filed #2894, a lane with `MERGE ==
  completed` still being offered a merge dispatch.
- row 8f (`/do-pr-review`) — re-reviewing #2734 and #2741 after they merged.
- row 5 (`/do-build`) — rebuilding a lane whose PR had already shipped.
- row 1 (`/do-plan`) — #2853's ledger emptied post-merge, and with no
  `pr_number` on record the router fell through to the fresh-lane row and
  began dispatching `/do-plan` on work that was already done.

Terminality therefore belongs in a guard, not a row: writing "and also stop if
this lane already shipped" into every row that could plausibly fire on a
finished lane means writing it four times today and rewriting it for every row
added later. A guard is the one place a finished lane can be recognized once.

## Why the guard preempts the dispatch table rather than living in a row

`guard_terminal_lane` is evaluated FIRST, ahead of every other guard and ahead
of the dispatch table itself (`agent/sdlc_router.py::GUARDS`, row `T`). A
finished lane has no correct dispatch and no guard verdict worth computing —
G1 through G9 all answer "given that this lane still has work to do, what
should happen next", which is the wrong question once MERGE has settled or the
PR has merged. Putting the check ahead of the table means every row, guard,
and future addition inherits terminal detection for free instead of each
having to re-derive it.

The tradeoff this buys is also its risk: because the guard preempts
*everything*, a false positive doesn't misroute one row — it halts a live lane
entirely, silently, with no dispatch and no escalation. `Terminal.evidence`
exists specifically so a wrong verdict is greppable in the logs
(`guard_terminal_lane` logs at INFO, not debug, on every terminal decision)
rather than indistinguishable from a legitimately finished lane. See
`SDLC_TERMINAL_GUARD` below for the mitigation.

## What makes a lane terminal

`agent.sdlc_router._terminal_evidence(stage_states, meta)` returns one of two
evidence strings, or `None`:

1. `"merge_marker"` — `stage_states["MERGE"]` is in `SETTLED_STATUSES`
   (`completed` or `skipped`). The pipeline's own bookkeeping says MERGE ran.
2. `"merged_pr"` — `meta["pr_state"] == "MERGED"`. GitHub says the tracking PR
   merged. This is the branch that survives a lost ledger: #2853 merged, then
   its ledger emptied, and the router began dispatching `/do-plan` on shipped
   work with no marker to consult. `meta["pr_state"]` is resolved even for a
   ledger-less lane by `tools.sdlc_stage_query._ledger_less_merged_pr` (see
   below).

`pr_merge_state` (GitHub's `mergeStateStatus`) is deliberately NOT consulted
for terminality — see the three meanings of `UNKNOWN` below.

When neither branch fires, `guard_terminal_lane` returns `None` and the
dispatch table runs as usual. Two negative controls matter most here, since
the guard preempts the whole table: a live pre-merge lane (`pr_state=OPEN`)
still dispatches `/do-merge` normally, and a fresh lane (empty stages, empty
meta) still dispatches `/do-plan`.

## The three meanings of `pr_merge_state == "UNKNOWN"`

GitHub's `mergeStateStatus` field reports `UNKNOWN` for three different
situations that are otherwise indistinguishable from the field alone:

1. **Merged.** A merged PR's mergeability is a meaningless question — GitHub
   returns `UNKNOWN` because there's nothing left to compute.
2. **Not yet computed.** GitHub computes `mergeStateStatus` asynchronously; a
   freshly opened or freshly rebased PR can read `UNKNOWN` for a few seconds
   before settling to `CLEAN`/`DIRTY`/etc. This is the case
   `tools.sdlc_stage_query._fetch_pr_merge_state`'s retry loop exists for —
   see `test_open_pr_still_retries_unknown` — but the retry is deliberately
   skipped for a PR whose `state == "MERGED"`
   (`test_merged_pr_skips_the_unknown_retry`), because a merged PR's `UNKNOWN`
   can never settle into anything else; retrying just sleeps.
3. **Genuinely unresolvable.** GitHub itself cannot determine mergeability
   (rare, but real) and `UNKNOWN` is the final, settled answer.

Only `pr_state` (GitHub's `state` field: `OPEN`/`CLOSED`/`MERGED`) separates
case 1 (merged-and-done) from cases 2 and 3 (not merged, and possibly still in
flight). That is why `_terminal_evidence` reads `pr_state`, never
`pr_merge_state`, to decide terminality.

## Resolving `pr_state` for a ledger-less lane

`tools.sdlc_stage_query._ledger_less_merged_pr(issue_number)` answers "is
there a PR for this issue?" when the normal ledger/session resolution path
(`_resolve_issue_record`) finds nothing — the #2853/#2812 shape where a lane's
`PipelineLedger` was evicted after it shipped.

It runs a two-pass lookup, mirroring `_compute_meta`'s ordering for the same
reason: **an OPEN PR is the lane's live artifact and must always win over a
historical one.** The function tries `state="open"` first; only when that
finds nothing does it fall back to `state="merged"`. Without this ordering, an
issue with both a merged PR (an earlier round shipped) and a live open PR (a
later round in flight), whose ledger has been evicted, would resolve to the
historical merged PR, read `pr_state == "MERGED"`, and `guard_terminal_lane`
would declare the lane finished while its open PR is still being worked — the
plan's Risk 1, reachable through the one path where terminality is inferred
rather than recorded.

The result is cached (`_ledgerless_pr_cache`, TTL via `SDLC_LEDGERLESS_PR_TTL`,
default 300s) and the cache stores the negative result too: the router polls
this path constantly and cannot tell a shipped-but-evicted lane from a
brand-new issue without asking GitHub, so remembering "no PR" as durably as
"yes" is what keeps a fresh issue from paying a `gh` subprocess on every poll.
Expired entries are swept on every write (`_evict_expired_ledgerless_entries`)
so the dict stays bounded to "issues polled within one TTL window" in a
long-lived process, rather than growing for every distinct issue number ever
polled.

The function is fail-open by design: any lookup failure returns `(None,
None)`, reproducing pre-#2894 behavior exactly. A lookup problem must never
manufacture a terminal verdict.

## Kill switch

Set `SDLC_TERMINAL_GUARD=false` to disable the guard, restoring the pre-#2894
routing. The switch is read live inside `guard_terminal_lane`
(`agent.sdlc_router._terminal_guard_enabled()`), not cached at import time —
`agent/sdlc_router.py` is imported once by the long-lived bridge/worker
(`agent/session_runner/runner.py`), so a live read means the override takes
effect on the very next call in that process, no service restart required.

## Files

| File | Purpose |
|------|---------|
| `agent/sdlc_router.py::Terminal` | The decision dataclass — `reason`, `evidence`, `row_id="T"` |
| `agent/sdlc_router.py::guard_terminal_lane` | The guard, evaluated first in `GUARDS` |
| `agent/sdlc_router.py::_terminal_evidence` | The two-branch predicate (`merge_marker` / `merged_pr`) |
| `agent/sdlc_router.py::_terminal_guard_enabled` | Live env-var read for the kill switch |
| `tools/sdlc_stage_query.py::_ledger_less_merged_pr` | Ledger-less `pr_state` resolution, open-before-merged, cached fail-open |
| `tools/sdlc_next_skill.py` | Emits `decision: "terminal"` as a third output shape alongside `dispatch`/`blocked` |
| `.claude/skills-global/do-sdlc/SKILL.md` | Treats `decision: "terminal"` as a clean exit, distinct from a `blocked` error stop |
| `tests/unit/sdlc_router_decision/test_sdlc_router_decision_terminal.py` | Guard behavior, kill switch, negative controls |
| `tests/unit/test_sdlc_stage_query.py::TestPrStateAndLedgerLessResolution` | `pr_state` resolution and the ledger-less rung |

## Related

- [SDLC Router Oscillation Guard](sdlc-router-oscillation-guard.md) — the G1–G9
  guard table this guard runs ahead of.
- [SDLC Pipeline](sdlc-pipeline.md) — the dispatch table and `_meta` field
  reference.
- [Pipeline State Machine](pipeline-state-machine.md) — `SETTLED_STATUSES` and
  stage status semantics.
