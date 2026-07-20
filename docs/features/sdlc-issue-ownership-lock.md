# SDLC Issue Ownership Lock

Redis-backed mutual-exclusion lock (issue #1954, run-identity model added by
issue #2003, doubles as the issue-keyed ledger's write-lease as of issue
#2012) preventing two independent SDLC entry points -- a local CLI
session driving `/do-sdlc`/`/sdlc`, and the standalone worker process driving
the same issue through the headless PM+dev runner -- from concurrently
driving pipeline work on the same GitHub issue.

**Write-lease unification (issue #2012).** The lock does double duty as of
issue #2012: it is no longer only an ownership mutex, it is also the sole
write authority over the durable, issue-keyed `PipelineLedger` (see
[`docs/features/sdlc-issue-keyed-stage-ledger.md`](sdlc-issue-keyed-stage-ledger.md)).
The lock payload gained a `target_repo` field so the ledger's composite key
`(target_repo, issue_number)` never needs to be re-resolved per write/read --
see "The `target_repo` Field" below.

## Problem Solved

Before this feature, no issue-level ownership concept existed anywhere in the codebase. Two independent SDLC entry points could each resolve and drive a pipeline for the same GitHub issue with no way to see each other. This wasn't hypothetical: on issue #1915, a worker-driven `sdlc-local-1915` session failed and was revived, then picked back up by the worker's normal pickup loop -- while, independently, a local CLI session had begun driving the same issue in a different worktree. The worker-driven session finished first and merged a PR. The local CLI session had no way to learn this, kept driving its stale worktree through BUILD-stage dispatches, and eventually opened a second, substantively duplicate PR, which had to be manually identified, closed, and cleaned up.

This feature closes that gap: any session attempting to start or continue pipeline work on an issue checks a shared Redis lock first. If another live session already owns the issue, the caller steps aside instead of duplicating the work. A session whose owner has genuinely died or gone stale does not permanently block the issue -- the lock expires on a TTL and renews only while the owner is actively working.

A first implementation (#1954) compared ownership by a per-process token. It self-collided the day it shipped (#1971) because `/do-sdlc` fans out to short-lived `sdlc-tool` subprocesses -- each a fresh OS process with its own token, so a supervision run blocked against itself. The patch was an `SDLC_HOLDER_TOKEN` env seam threaded through skill-body prose. Issue #2003 replaced that seam with a modeled identity -- `run_id` -- described below.

## Run Identity: `run_id` (issue #2003)

The unit of ownership is a **pipeline run**, not an OS process and not a session record. `run_id` is a `uuid.uuid4().hex` string, one per logical top-level supervision run (a single `/do-sdlc N` invocation, or one worker-driven pipeline execution).

### Minting is exclusive to `ensure_session`, decided by the lock

`tools/sdlc_session_ensure.py::ensure_session()` is the **only** place a `run_id` is minted. Immediately before *every* one of its return points -- cold-start create, or any of its early-return branches -- `_acquire_run_lock_and_bind()` generates a fresh uuid-hex candidate and contests the issue lock with `touch_issue_lock(issue_number, candidate, ...)` (a `SET NX EX` carrying the candidate).

- **Lock acquired** → this run owns the issue. The candidate is saved to `AgentSession.active_run_id` and returned in the JSON output: `{"session_id": ..., "created": ..., "run_id": "<hex>"}`.
- **Lock held by a foreign run_id** → `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id": ..., "owner_session_id": ..., "orphaned_lock": ...}`, regardless of what `active_run_id` the session *record* happens to carry. The `orphaned_lock` flag comes from a follow-up peek so callers can distinguish a healthy foreign owner from a ghost whose lock frees within the TTL.

**There is no adopt-from-record branch.** A round-1 critique BLOCKER named the failure mode directly: reading `active_run_id` off the shared, deterministically-keyed session record (`sdlc-local-{N}`) to decide "do I already own this" lets a second live supervisor calling `ensure_session` while the incumbent is still running silently impersonate it -- the exact #1915 collision one layer up. Minting is therefore keyed off the **lock's live holder**, never off `session.status`. Every top-level call is a fresh contest; the loser is told who the foreign owner is and steps aside.

### Verified reuse: `--reuse-run-id` (cycle-3 BLOCKER 1)

The per-stage `/sdlc` router runs `session-ensure` at the start of EVERY stage, and each stage's final `stage-marker --status completed` renews the lock to the full TTL. Without a reuse path, the router's next invocation seconds later would mint a fresh candidate, lose `SET NX` to its **own** prior stage's live lock, and treat the resulting `ISSUE_LOCKED` as a hard block -- self-wedging every multi-stage run at the first stage boundary.

`session-ensure --reuse-run-id <id>` is the verified continue path. The claimed run_id must arrive FROM the caller (the conversation that minted it), and it is honored only when the caller can prove continuity (`_validated_reuse_candidate()`):

1. **Live lock owner match** -- the lock's owner run_id equals the claim (the consecutive-stage case), or
2. **Free lock + record mirror match** -- the lock lapsed and `AgentSession.active_run_id` equals the claim (lossless recovery after a TTL lapse).

A verified claim renews/re-acquires under the same run_id; an unverified claim is silently ignored and falls through to the fresh-mint contest, where a live foreign holder still yields `ISSUE_LOCKED`. This is claim-echo with proof, never adoption: the code never reads a run_id OUT of the lock or record to hand to a caller.

### Cold-state write gate (cycle-3)

The four state-mutating subcommands resolve their session via `find_session(..., ensure=True, caller_run_id=<--run-id>)`. When the pure lookup finds **no session** and the caller carries a run_id, the auto-ensure branch is **skipped** and the write is quietly refused: a run_id is minted only by `ensure_session` (which creates and binds the record), so a run_id-carrying write with no session is stale by definition. Ensuring on its behalf would mint a fresh session + lock as a side effect of a write that is about to be refused anyway, wedging the next legitimate `session-ensure` behind `ISSUE_LOCKED` for up to the TTL. Identity-less programmatic callers (no run_id) keep the #1558/#1671 auto-ensure behavior.

### Mid-run identity travels explicitly, never ambiently

Once minted, `run_id` is **not** re-derived, re-exported, or adopted anywhere else in a run. It is carried explicitly:

- `session-ensure` emits it once in its JSON output.
- The invoking skill (`/do-sdlc`, `/sdlc`) reads it from that output and passes `--run-id {run_id}` on every subsequent state-mutating `sdlc-tool` call for the rest of the run.
- The standalone worker's in-process path (`agent/session_executor.py`) passes the same run_id programmatically -- no CLI, no env var.

A state-mutating `sdlc-tool` subcommand invoked **without** `--run-id` fails loudly with a named non-zero error (`RUN_ID_REQUIRED`) instead of minting a fresh identity or silently adopting the record's `active_run_id`. This is the structural fix for the #1971 failure mode: a missing identity is a hard stop, never a new, unintentionally-independent owner.

Gated this way: `sdlc-tool dispatch record`, `sdlc-tool stage-marker`, and `sdlc-tool verdict record` all require `--run-id` and refuse to run without it (`requires_run_id=True` on their CLI subparsers). `sdlc-tool meta-set`'s state-mutating writes require it too. Read-only subcommands (`stage-query`, `next-skill`, `verdict get`, `dispatch get`) take no run-id at all.

### The `touch_issue_lock` primitive

`models/session_lifecycle.py::touch_issue_lock(issue_number, run_id, session_id="", ttl=ISSUE_LOCK_TTL_SECONDS, peek=False) -> IssueLockResult` is modeled directly on the existing `claim_pending_run()` claim (issue #1817): a plain, non-Popoto-managed Redis key using the same `SET NX EX` idiom and the same fail-open-on-error behavior.

**Redis key**: `session:issuelock:{issue_number}`

**Stored payload** (JSON):

```json
{"run_id": "a1b2c3...", "session_id": "sdlc-local-1954", "pid": 42317, "hostname": "worker-1", "target_repo": "owner/repo"}
```

Ownership is decided **solely** by comparing the caller's `run_id` against the payload's `run_id` -- a fresh live check on every mutation. `session_id`/`pid`/`hostname` ride along purely for human-readable display (`owner_session_id` in `IssueLockResult` and in the blocked-dispatch JSON shape) -- they are never compared for ownership, because two independent live processes can resolve the identical deterministic `session_id` (`sdlc-local-{issue_number}`) for the same issue. `target_repo` (issue #2012) rides along for a different purpose: it is the single authoritative source the issue-keyed `PipelineLedger`'s writers and readers use to assemble their `(target_repo, issue_number)` key, so no writer or reader needs to shell out to `gh repo view` per call.

### The `target_repo` Field (issue #2012)

`touch_issue_lock()` gained a `target_repo: str | None = None` parameter, and `IssueLockResult` gained a matching `target_repo` field. Both exist to support the ledger write-lease unification above, not the ownership decision itself -- ownership is still decided solely by `run_id` comparison.

**Where it's pinned.** `tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind()` is the ONE place `target_repo` is resolved for this purpose: it calls `tools/_sdlc_utils.py::_resolve_target_repo()` (the `GH_REPO` env → `SDLC_TARGET_REPO`-as-cwd → git-toplevel resolution ladder, set authoritatively by `sdk_client.py`) exactly once per `ensure_session()` call, then passes the result into every `touch_issue_lock()` call it makes for that call -- both the acquiring call and the follow-up orphan-peek. This is the one place the process env is trustworthy regardless of a takeover session's foreign slug or cwd; writers and readers downstream never re-resolve it themselves. A `None` resolution is passed through as-is -- lock acquisition is never blocked on repo resolution; a missing pinned repo becomes an *observable* degradation downstream in the ledger's writers/readers (see the sibling doc's Risk 5 section), not a blocker here.

**Self-healing renewal.** The same-owner renewal branch inside `touch_issue_lock()` (`models/session_lifecycle.py`, around the `payload.get("run_id") == run_id` check) used to be a bare `_R.expire(key, ttl)` -- it only extended the TTL and never touched the payload. Under the issue-keyed ledger's hard-fail write design, a lock that predates `target_repo` pinning and simply keeps renewing forever would *never* gain the field, hard-failing every stage write across the cutover. The fix: same-owner renewal now re-`SET`s the **full** payload -- spreading the existing payload (`{**payload, "target_repo": ...}`, never reconstructing a subset, so `pid`/`hostname` survive untouched) and overriding only `target_repo` with the caller's freshly-resolved value when given, else falling back to whatever the payload already carried. A lock acquired before this deploy therefore self-heals and gains `target_repo` on its very next renewal tick -- no separate backfill of live locks is needed (the `PipelineLedger` migration, described in the sibling doc, is a one-time data lift, unrelated to this in-place lock self-heal).

**Read access.** Both writers and readers of the issue-keyed ledger read `target_repo` from the lease rather than resolving it themselves:

- Writers (`sdlc-tool stage-marker`, `verdict record`, `meta-set`, `dispatch record`) call `tools/_sdlc_utils.py::resolve_ledger_lease(issue_number, run_id)`, which peeks the lock, confirms `run_id` is the live owner, and returns the pinned `target_repo`.
- Readers (`stage-query`, `verdict get`, `dispatch get`, the dashboard) call `tools/_sdlc_utils.py::resolve_target_repo_for_read(issue_number)`, which peeks the lock with `run_id=None` (any live lease's pinned value is visible regardless of who holds it) and falls back to `_resolve_target_repo()`'s env-first ladder only when no live lease exists at all (a cold read after TTL lapse).

Neither side ever assembles a `PipelineLedger` key with a `None` repo component -- see the ledger doc's Risk 5 discussion for the full writer/reader guard contract.

### Behavior

| Scenario | Result |
|---|---|
| No existing key, caller supplies a `run_id` | `SET NX EX` claims it. `acquired=True`. |
| Existing key, same `run_id` | Renews by re-`SET`ting the full payload (self-healing `target_repo` pin, issue #2012 -- see above), not a bare `EXPIRE`. `acquired=True`. |
| Existing key, different `run_id` | A foreign live run owns it. `acquired=False`, with the owner's `run_id`/`session_id` surfaced for display. |
| Mutation call with **no `run_id` supplied at all** | Never mints or acquires -- reports the current holder (`acquired=True` if the key is unheld, `False` with the owner surfaced if held). Minting is exclusive to `ensure_session`. |
| Malformed/unparseable (non-JSON) existing value | Treated as a foreign, non-matching holder. `acquired=False`, `owner_run_id=None`. Never raises on `json.loads`. |
| `SET NX` loses the race, but the key expires before the follow-up `GET` | Treated as free; this attempt succeeds (`acquired=True`). |
| `issue_number` falsy (`None` or `0`) | No-op: fails open (`acquired=True`) without touching Redis. |
| Any Redis exception | Fails **open**: `acquired=True`, logged at `warning` with the swallowed error class. Mirrors `claim_pending_run()` -- a Redis hiccup degrades to no cross-process protection rather than wedging the SDLC pipeline. |

Fail-open on infra errors, fail-closed on genuine contention: a Redis hiccup never blocks progress, but a live competing holder is a hard stop.

Stale-owner takeover keeps the existing TTL semantics: an expired lock is claimable by the next fresh candidate; no takeover reads `active_run_id` as authority.

### TTL

`ISSUE_LOCK_TTL_SECONDS` (env-overridable via `ISSUE_LOCK_TTL_SECONDS`, default `300`) lives next to `RUN_CLAIM_TTL_SECONDS` in `models/session_lifecycle.py`. It is sized at 5x the worker's 60s heartbeat interval so renewal cadence gives generous margin before expiry.

### Peek mode and `orphaned_lock`

`peek=True` reports the current lock state (same `run_id` comparison) **without** acquiring, renewing, or otherwise mutating the lock. An unheld key reports `acquired=True, owner_run_id=None`.

When a peek finds the lock held by a foreign `run_id`, it also reports `orphaned_lock`: `True` when that `run_id` matches no live (non-terminal) session's `active_run_id` -- i.e. the owning run died between acquiring the lock and its next renewal (bounded by the TTL; see Race 3 below). `_run_id_has_live_session()` fails toward `False` (not orphaned) on any lookup error, so a Redis/ORM hiccup never mislabels a healthy owner as a ghost.

Peek is used by two read-only checkpoints -- a routing *decision* must never itself claim or extend a lock:

- `tools/sdlc_next_skill.py::decide()`'s pre-check, ahead of all G1-G8 guard evaluation. It peeks with the identity read back from the resolved issue session's own `active_run_id` (not a caller-supplied `--run-id` -- `next-skill` is a read-only subcommand).
- `tools/sdlc_dispatch.py::_peek_issue_lock_conflict()`, called by `dispatch record`'s CLI wrapper after a write failure, to disambiguate lock contention from an unrelated write error.

### Compare-and-delete release

`release_issue_lock(issue_number, run_id) -> bool` deletes `session:issuelock:{issue_number}` **only if** the stored payload still carries the given `run_id` -- a Lua value-compare (`GET` then conditional `DEL` in one script), never a raw `DEL`. A raw delete could race a successor's freshly-acquired lock and destroy the wrong owner's claim; compare-and-delete makes that impossible. Used by `ensure_session`'s crash-window cleanup (below) and available to any future caller that needs to release its own lock early.

## Run/Record Consistency and Crash-Window Recovery

After `session.active_run_id = candidate; session.save()` in the acquire path, `_acquire_run_lock_and_bind()` re-reads the session record and asserts `active_run_id` matches the candidate it just wrote (post-save readback). On save failure or a readback mismatch, the lock is released via compare-and-delete and the caller gets `{"error": "RUN_BIND_FAILED", "reason": ..., "session_id": ...}` -- the next caller can acquire immediately instead of waiting out the full TTL.

This covers the save-failure branch precisely; it cannot cover a true process death *inside* the acquire→save window (nothing is running to do the readback). That case is bounded by the lock's TTL and made visible, not silent: the peek path reports `orphaned_lock: true` for a lock whose `run_id` matches no live session, so an operator or the router sees "held by a ghost" rather than "held by a healthy foreign owner."

### Recovery after run_id loss

If a local supervisor loses track of its `run_id` (context compaction, crash of the driving CLI session), there is deliberately **no adopt-from-record shortcut**. The documented recovery is: re-run `sdlc-tool session-ensure`. While the old lock is still live, this returns `ISSUE_LOCKED` (bounded by the ≤300s TTL, since nothing is renewing the orphaned run's lock); once the TTL lapses, a fresh contest succeeds and mints a new `run_id`. Bounded and loud -- an operator sees a named block, not a silent split-identity continuation. A caller that still HAS its run_id recovers immediately with `--reuse-run-id` (verified reuse above) -- no TTL wait, same identity.

## The Two In-Process Renewal Paths

Two call sites renew the lock without going through a `sdlc-tool` CLI subprocess. Both source their identity from `agent_session.active_run_id` -- the read-back of the identity *this same pipeline's own* `ensure_session()` established -- never a foreign adoption, so neither violates the no-adopt rule above.

- **`tools/_sdlc_utils.py::renew_issue_lock_for_session(session, run_id=None)`** -- wired into `tools/sdlc_stage_marker.py::write_marker()`. Falls back to `session.active_run_id` when no explicit `run_id` is passed. Best-effort: logs and returns on any failure, never blocks or alters the marker write's outcome.
- **`agent/session_executor.py::_tick_issue_lock_renewal()`** -- the worker's 60s heartbeat renewal for `session_type == "eng"` sessions with a resolved `issue_number`. Cycle-3 BLOCKER 2: the identity is **re-fetched from Redis on every tick** (`_fetch_live_active_run_id()`, one indexed Popoto query) -- the executor's in-memory `agent_session` snapshot was fetched once at session start, *before* the session-ensure subprocess wrote `active_run_id`, so reading the snapshot attribute is permanently stale (None on fresh runs → renewal skips forever and the lock lapses mid-stage; the previous run's id on resumed sessions → a lapsed lock gets re-acquired under a dead identity and renewed forever). Skips renewal for that tick (with a debug log) when the record carries no live `active_run_id` or the fetch fails -- an identity-less tick must never extend or mint a lock. When a renewal attempt comes back `not acquired` (a foreign run now holds the lock), it logs at **WARNING** -- no longer purely fire-and-forget -- so an out-from-under takeover is visible before the TTL lapses.

## The `issue_number` Mirror Field

`models/agent_session.py` adds `issue_number = IntField(null=True)` to `AgentSession` -- a **read-side visibility mirror only**, unrelated to `run_id`. It is written exactly **once**, at session creation inside `ensure_session()`'s create-and-claim path (parsed from `issue_url`), and is never re-written on any lock renewal call. It never gates any decision -- only the Redis key does. The field exists purely so the lock's owner is visible via `valor_session inspect` / `sdlc-tool stage-query` without requiring a Redis lookup, and so the heartbeat renewal guard has a cheap in-memory field to check instead of re-deriving the issue number from `issue_url` on every 60s tick.

No backfill was performed on existing historical `AgentSession` records (out of scope) -- the field is nullable and populated going forward only.

## `find_session_by_issue()`'s `include_terminal` Parameter

`tools/_sdlc_utils.py::find_session_by_issue(issue_number, include_terminal=False)` previously had no status filter at all -- it could and did return a terminal (`failed`/`completed`/`killed`) session as "the" owner of an issue. This was directly part of how incident #1915 happened: the terminal `sdlc-local-1915` record was revived and picked up while a second, independent session already believed it owned the issue.

All three resolution passes (`issue_url` ownership, deterministic-id `sdlc-local-{N}`, `message_text` fallback) now exclude sessions whose `status` is in `{"failed", "completed", "killed"}` by default. Callers that want live-ownership resolution -- the common case: `ensure_session`, routing -- get only non-terminal sessions. Callers that explicitly want history (audit/debug tooling) opt in with `include_terminal=True`.

## Bridge-owned run adoption (issue #2026, WS-F)

`ensure_session`'s env short-circuit (`VALOR_SESSION_ID` / `AGENT_SESSION_ID`) resolves the live bridge PM eng session that spawned the dev subagent driving the pipeline. The #1147 dedup contract keeps that env session "without creating anything" **only when it already owns the issue** by `issue_url` (`.endswith("/issues/{N}")`). But a bridge PM session built from raw message text -- e.g. Tom's `"SDLC 1312"` -- never gets `issue_url` stamped, so the ownership check missed, and `find_session_by_issue`'s `message_text` fallback regex (`\bissue\s*#?\s*{N}\b`) also missed because the bare form carries no literal word "issue". `ensure_session` then fell through and minted a **second, unlinked `sdlc-local-{N}`** for an issue the PM session already owned -- the split-brain that manufactures the very gate/verdict/lease races WS1–WS-E exist to survive.

**The fix:** when the resolved env session is a live, non-terminal eng session whose `issue_url` is **ownerless** (empty, `None`, or whitespace-only -- tested with `.strip()`), `ensure_session` **adopts** it as the run owner instead of minting a competitor:

1. `_acquire_run_lock_and_bind(issue_number, resolved, reuse_run_id=...)` acquires the issue lock, binds the fresh `run_id` to `resolved.active_run_id` (with the post-save readback), and writes the supervised-run signal -- all the normal ownership machinery.
2. **Only on the bind's success**, `resolved.issue_url` is stamped (from the passed `--issue-url`, or built as `https://github.com/{repo}/issues/{N}` from the resolved target-repo slug) and `save()`d as the **last** write.
3. Returns `{"session_id": <env_session>, "created": false, "run_id": ...}` -- no `sdlc-local-{N}` is minted.

**Bind-first, stamp-last** is load-bearing: a bind failure (`RUN_BIND_FAILED`, or a *foreign* `ISSUE_LOCKED`) leaves `issue_url` untouched and returns the error dict verbatim -- it never falls through to `create_local`, because under a held foreign lock that would mint the exact orphan WS-F prevents (then fail `ISSUE_LOCKED` anyway). A stamp failure *after* a successful bind is benign: the run is already correctly owned, so the adopted session is returned and a later ensure re-stamps idempotently via the self-owned `ISSUE_LOCKED` + `--reuse-run-id` recovery path (the `issue_url` stamp is a best-effort findability optimization, not the ownership record -- the lock is).

**Divergent-owner protection preserved (#1671/#1672):** adoption fires *only* for an ownerless session. An env session that already owns a **different** issue (`issue_url` set to `/issues/{M}`, M != N) keeps the existing fall-through to `find_session_by_issue`, so a forked subagent inheriting a parent's `VALOR_SESSION_ID` never has its issue reassigned.

`sdlc-local-{N}` is still minted in the #1558 case it was built for -- when no live eng session owns the issue at all.

## Renewal Call Sites

Every mutation-adjacent checkpoint in the SDLC pipeline touches the lock. This is deliberately broader than "just where the lock is first acquired" -- a single stage (e.g. BUILD) can run far longer than the gap between dispatch-time touches, so the lock must be kept alive by whichever mechanism is already firing regularly on that path.

| Call site | File | Trigger | Identity source |
|---|---|---|---|
| `ensure_session()` -- all return points | `tools/sdlc_session_ensure.py` | Every session resolution for an issue (cold-start create, or any early-return branch) | Mints a fresh candidate per top-level call via `_acquire_run_lock_and_bind()`, invoked immediately before *each* `return` so no branch can silently skip it. |
| `record_dispatch_for_session()` -- direct call | `tools/sdlc_dispatch.py` | Before writing every dispatch event (i.e. before every sub-skill invocation) | The caller's explicit `run_id` (CLI `--run-id`), falling back to `session.active_run_id` for in-process callers. An issue-scoped session with **no** run identity at all refuses the write outright. |
| `decide()` peek pre-check | `tools/sdlc_next_skill.py` | Every `sdlc-tool next-skill` call, before G1-G8 guard evaluation | Read-only: peeks with the identity read back from the resolved issue session's `active_run_id`. Never acquires or renews. |
| `_tick_issue_lock_renewal` | `agent/session_executor.py` | Every 60s heartbeat tick, for a worker-driven `session_type == "eng"` session with a resolved `issue_number` | `active_run_id` re-fetched from Redis each tick via `_fetch_live_active_run_id()` (read-back only; skips the tick if absent or fetch fails). Warns at WARNING on a not-owner result. |
| `sdlc-tool stage-marker` write | `tools/sdlc_stage_marker.py` (via `tools/_sdlc_utils.py::renew_issue_lock_for_session()`) | Every stage-marker write (BUILD/TEST/REVIEW stage transitions) | The CLI's `--run-id`, falling back to `session.active_run_id`. Fires after the ownership guard and before the state-machine write; best-effort, never blocks or alters the write outcome on failure. |

### Gated but not renewed: `verdict record`, `meta-set`

`sdlc-tool verdict record` and `sdlc-tool meta-set` both **require** `--run-id` (a missing flag is `RUN_ID_REQUIRED`) and both **gate** their writes through `tools/_sdlc_utils.py::check_run_ownership()` -- a peek-only check that refuses the write with an `ISSUE_LOCKED` diagnostic when a foreign run holds the issue. Neither, however, **renews** the lock's TTL. Both fire during PLAN/CRITIQUE-stage bookkeeping or ad hoc metadata writes with no established recurrence path through an in-progress BUILD/TEST/REVIEW stage -- renewing there would be speculative rather than load-bearing. If operational experience later surfaces a concrete long-running gap through one of these two, add renewal there as a follow-up rather than pre-wiring it speculatively now.

## The `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` Shape

When a lock check finds the issue owned by a different live run, the caller surfaces a blocked signal parallel to the existing G-guard `blocked` shape (`{"blocked": true, "reason": ..., "guard_id": ...}`) used elsewhere in SDLC routing.

`/sdlc` and `/do-sdlc` treat this exactly like a guard block: surface `reason`, `owner_run_id`, and `owner_session_id` to the human, do not loop, do not attempt to route around it by guessing an alternative skill. See `.claude/skills/sdlc/SKILL.md`'s ISSUE_LOCKED guard documentation for the pipeline-level interpretation contract -- not duplicated here.

### Where the literal JSON shape is actually emitted

Two call sites emit the full `{"blocked": true, ...}` shape:

- **`tools/sdlc_session_ensure.py::ensure_session()`** -- any of its return points returns `{"blocked": True, "reason": "ISSUE_LOCKED", "owner_run_id": ..., "owner_session_id": ...}` in place of the normal `{"session_id": ..., "created": ..., "run_id": ...}` payload when `touch_issue_lock()` reports contention.
- **`tools/sdlc_next_skill.py::decide()`** -- the peek pre-check returns `{"blocked": True, "reason": "ISSUE_LOCKED", "guard_id": "ISSUE_LOCK", "owner_run_id": ..., "owner_session_id": ..., "orphaned_lock": ...}` before any G1-G8 guard runs.

`dispatch record` surfaces the same lock information through a different shape, described next -- it never returns `blocked: true`.

### `dispatch record` merges the reason into its existing `ok: false` shape (does not use `blocked`)

`record_dispatch_for_session()` (`tools/sdlc_dispatch.py`) intentionally stays a plain `bool` -- other call sites and existing tests already depend on that return-type contract -- so a `False` result alone is ambiguous: issue-lock contention and any other write failure (e.g. a Redis write conflict inside `update_stage_states`) both collapse to the same `False`.

The CLI wrapper (`sdlc-tool dispatch record`, implemented by `_cli_record()`) disambiguates this after the fact: on a failed write (`ok: false`), it calls `_peek_issue_lock_conflict()` -- a read-only, non-mutating `touch_issue_lock(peek=True)` check that re-derives the issue number from `session.issue_url` the same way `record_dispatch_for_session()` does internally. If the peek shows the lock held by a different live run, `reason`, `owner_run_id`, and `owner_session_id` are merged into the result dict alongside the pre-existing keys:

```json
{"ok": false, "history_length": 3, "reason": "ISSUE_LOCKED", "owner_run_id": "a1b2c3...", "owner_session_id": "sdlc-local-1954"}
```

A write failure unrelated to the lock (or a session with no parseable issue number) keeps the original `{"ok": false, "history_length": N}` shape unchanged -- this is purely additive, not a new top-level contract. A successful write (`ok: true`) never triggers the peek at all.

`/sdlc` and `/do-sdlc` treat either shape (`blocked: true` from `ensure_session()`/`next-skill`, or `ok: false` + `reason: "ISSUE_LOCKED"` from `dispatch record`) identically: surface `reason` and the owner identifiers to the human, do not loop, do not attempt to route around it by guessing an alternative skill.

## Interaction with Crash Recovery

A revived terminal session (via `reflections/crash_recovery.py`'s auto-resume or a manual `valor-session resume`) that finds its issue already owned by a live peer steps aside at the same checkpoints described above -- `ensure_session()` and `record_dispatch_for_session()` both run before any real BUILD-or-later work happens on the revived session's next turn. No separate pre-revival gate was added inside `crash_recovery.py` itself. See [Session Recovery Mechanisms](session-recovery-mechanisms.md) for the full recovery-mechanism catalogue and the corresponding race-condition entry.

## Hard Cutover (issue #2003)

`_process_holder_token()`, the `SDLC_HOLDER_TOKEN` environment seam, and the gitignored `data/.sdlc_run/` run-file convention are **deleted entirely** -- no compatibility alias, no dual-read window. `grep -rn "SDLC_HOLDER_TOKEN\|\.sdlc_run" tools/ models/ agent/ .claude/skills/ .claude/skills-global/` returns nothing.

The deploy runbook pairs the merge with an immediate worker restart: `./scripts/valor-service.sh worker-restart`. The worker is a single process, so no in-flight session survives the restart boundary carrying a stale token. A local `/do-sdlc` supervision run in flight at deploy time fails loudly at its next state-mutating call (missing or foreign run identity) rather than continuing silently on a token the new code no longer recognizes; the recovery path is the same as any other run-id loss (above) -- re-run `session-ensure`, bounded by the ≤300s lock TTL.

## Source Files

| File | Role |
|---|---|
| `models/session_lifecycle.py` | `touch_issue_lock()`, `release_issue_lock()`, `_run_id_has_live_session()`, `IssueLockResult`, `ISSUE_LOCK_TTL_SECONDS` |
| `models/agent_session.py` | `issue_number` mirror field; `active_run_id` field |
| `tools/sdlc_session_ensure.py` | `ensure_session()`'s return-point wiring; `_acquire_run_lock_and_bind()` (exclusive run_id mint site) |
| `tools/_sdlc_utils.py` | `find_session_by_issue(include_terminal=...)`, `renew_issue_lock_for_session()`, `check_run_ownership()`, `resolve_ledger_lease()`/`revalidate_ledger_lease()` (writer lease checks, issue #2012), `resolve_target_repo_for_read()` (reader lease-first repo resolution, issue #2012) |
| `agent/pipeline_ledger.py` | `PipelineLedger` -- the durable, issue-keyed record this lock's `target_repo` field authorizes writes to (issue #2012; see [`docs/features/sdlc-issue-keyed-stage-ledger.md`](sdlc-issue-keyed-stage-ledger.md)) |
| `tools/sdlc_dispatch.py` | `record_dispatch_for_session()`'s direct lock call; `_peek_issue_lock_conflict()` + `_cli_record()`'s post-failure disambiguation; `--run-id` CLI flag |
| `tools/sdlc_stage_marker.py` | `--run-id` CLI flag; ownership guard + renewal call |
| `tools/sdlc_verdict.py` | `--run-id` CLI flag on `verdict record`; ownership guard (no renewal) |
| `tools/sdlc_meta_set.py` | `--run-id` CLI flag on state-mutating keys (e.g. `pr_number`); ownership guard (no renewal) |
| `tools/sdlc_next_skill.py` | `decide()`'s peek pre-check |
| `agent/session_executor.py` | `_tick_issue_lock_renewal()` (tier-1 heartbeat, `active_run_id` read-back) |
| `.claude/skills/sdlc/SKILL.md` | `ISSUE_LOCKED` guard interpretation contract for `/sdlc`/`/do-sdlc`; `--run-id` threading rules |
| `.claude/skills-global/do-sdlc/SKILL.md` | Reads `run_id` from `session-ensure`'s JSON output and threads `--run-id` through every state-mutating `sdlc-tool` call for the run |
