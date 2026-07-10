# SDLC Issue Ownership Lock

Redis-backed mutual-exclusion lock (issue #1954) preventing two independent SDLC entry points -- a local CLI session driving `/do-sdlc`/`/sdlc`, and the standalone worker process driving the same issue through the headless PM+dev runner -- from concurrently driving pipeline work on the same GitHub issue.

## Problem Solved

Before this feature, no issue-level ownership concept existed anywhere in the codebase. Two independent SDLC entry points could each resolve and drive a pipeline for the same GitHub issue with no way to see each other. This wasn't hypothetical: on issue #1915, a worker-driven `sdlc-local-1915` session failed and was revived, then picked back up by the worker's normal pickup loop -- while, independently, a local CLI session had begun driving the same issue in a different worktree. The worker-driven session finished first and merged a PR. The local CLI session had no way to learn this, kept driving its stale worktree through BUILD-stage dispatches, and eventually opened a second, substantively duplicate PR, which had to be manually identified, closed, and cleaned up.

This feature closes that gap: any session attempting to start or continue pipeline work on an issue checks a shared Redis lock first. If another live session already owns the issue, the caller steps aside instead of duplicating the work. A session whose owner has genuinely died or gone stale does not permanently block the issue -- the lock expires on a TTL and renews only while the owner is actively working.

## The `touch_issue_lock` Primitive

`models/session_lifecycle.py::touch_issue_lock(issue_number, session_id, ttl=ISSUE_LOCK_TTL_SECONDS, peek=False) -> IssueLockResult` is modeled directly on the existing `claim_pending_run()` claim (issue #1817): a plain, non-Popoto-managed Redis key using the same `SET NX EX` idiom and the same fail-open-on-error behavior.

**Redis key**: `session:issuelock:{issue_number}`

**Stored payload** (JSON):

```json
{"holder_token": "a1b2c3...", "session_id": "sdlc-local-1954", "pid": 42317, "hostname": "worker-1"}
```

### Why ownership is compared by `holder_token`, not `session_id`

Both entry points resolve the *identical* deterministic `session_id` for a given issue: `tools/sdlc_session_ensure.py::ensure_session()` always derives `local_session_id = f"sdlc-local-{issue_number}"`, with no per-process or per-machine differentiator. If `touch_issue_lock` compared identity by `session_id`, two independently-live processes racing the same issue would each pass the same string and each conclude "I already own this" -- exactly the collision this lock exists to prevent (this was a BLOCKER finding in the plan's round-2 critique, reproducing the #1915 failure mode inside the fix itself).

The lock is therefore keyed on a **process-unique `holder_token`**: `uuid.uuid4().hex`, generated once per OS process and cached in a module-level variable (`_process_holder_token()`), stable for the life of that process. Two processes handling the same `session_id` -- on the same or different machines -- get distinct tokens; the same process calling repeatedly (dispatch -> heartbeat -> dispatch) reuses its own token and renews cleanly. `session_id`/`pid`/`hostname` ride along in the payload purely for human-readable display (`owner_session_id` in `IssueLockResult` and in the blocked-dispatch JSON shape) -- they are never compared for ownership.

### The `SDLC_HOLDER_TOKEN` env seam (issue #1971)

Per-process tokens have one failure mode: an SDLC entry point that fans out to **multiple short-lived subprocesses** blocks itself. The standalone worker calls `touch_issue_lock()` in-process (`agent/session_executor.py`), so it always holds one stable token. But a local `/do-sdlc` supervisor drives the router through separate `sdlc-tool` CLI subprocesses (`session-ensure`, then `next-skill`, then `dispatch record`), each a fresh OS process with its own random token. Process A (`session-ensure`) acquires the lock; process B (`next-skill`) peeks, sees A's token as foreign, and short-circuits to `ISSUE_LOCKED` -- the run blocks against itself before dispatching a single stage.

`_process_holder_token()` resolves `SDLC_HOLDER_TOKEN` from the environment first, falling back to the random uuid when it is unset or empty. The `/do-sdlc` skill mints one token per supervision run, persists it to a gitignored run file (`data/.sdlc_run/holder_{issue}`), and re-exports it before every state-mutating `sdlc-tool` call, so all of that run's subprocesses present one consistent owner. The worker never sets the var, so it keeps its random per-process token and the worker-vs-local guard (the #1915 case) is unchanged. Concurrent local supervisors on the *same* issue is out of scope for this seam (the later run overwrites the run file); the primary duplicate-PR guard is worker-vs-local, which stays intact.

### Behavior

| Scenario | Result |
|---|---|
| No existing key | `SET NX EX` claims it. `acquired=True`. |
| Existing key, same `holder_token` (this process already owns it) | Renews via `EXPIRE`. `acquired=True`. |
| Existing key, different `holder_token` | Another live process owns it. `acquired=False`, with the owner's `session_id` from the payload for display. |
| Malformed/unparseable (non-JSON) existing value | Treated as a foreign, non-matching holder. `acquired=False`, `owner_session_id=None`. Never raises on `json.loads`. |
| `SET NX` loses the race, but the key expires before the follow-up `GET` | Treated as free; this attempt succeeds (`acquired=True`). |
| `issue_number` falsy (`None` or `0`) | No-op: fails open (`acquired=True`) without touching Redis. |
| Any Redis exception | Fails **open**: `acquired=True`, logged at `warning`. Mirrors `claim_pending_run()` -- a Redis hiccup degrades to no cross-process protection rather than wedging the SDLC pipeline. |

Fail-open on infra errors, fail-closed on genuine contention: a Redis hiccup never blocks progress, but a live competing holder is a hard stop.

### TTL

`ISSUE_LOCK_TTL_SECONDS` (env-overridable via `ISSUE_LOCK_TTL_SECONDS`, default `300`) lives next to `RUN_CLAIM_TTL_SECONDS` in `models/session_lifecycle.py`. It is sized at 5x the worker's 60s heartbeat interval so renewal cadence gives generous margin before expiry.

### Peek mode

`peek=True` reports the current lock state (same `holder_token` comparison) **without** acquiring, renewing, or otherwise mutating the lock. An unheld key reports `acquired=True, owner_session_id=None`. This is used exactly once, by the `next-skill` routing pre-check (below) -- a routing *decision* must never itself claim or extend a lock; only mutation call sites do that.

## The `issue_number` Mirror Field

`models/agent_session.py` adds `issue_number = IntField(null=True)` to `AgentSession` -- a **read-side visibility mirror only**. It is written exactly **once**, at session creation inside `ensure_session()`'s create-and-claim path (parsed from `issue_url`), and is never re-written on any lock renewal call. It never gates any decision -- only the Redis key does. The field exists purely so the lock's owner is visible via `valor_session inspect` / `sdlc-tool stage-query` without requiring a Redis lookup, and so the heartbeat renewal guard (below) has a cheap in-memory field to check instead of re-deriving the issue number from `issue_url` on every 60s tick.

No backfill was performed on existing historical `AgentSession` records (out of scope, per the plan's No-Gos) -- the field is nullable and populated going forward only.

## `find_session_by_issue()`'s `include_terminal` Parameter

`tools/_sdlc_utils.py::find_session_by_issue(issue_number, include_terminal=False)` previously had no status filter at all -- it could and did return a terminal (`failed`/`completed`/`killed`) session as "the" owner of an issue. This was directly part of how incident #1915 happened: the terminal `sdlc-local-1915` record was revived and picked up while a second, independent session already believed it owned the issue.

All three resolution passes (`issue_url` ownership, deterministic-id `sdlc-local-{N}`, `message_text` fallback) now exclude sessions whose `status` is in `{"failed", "completed", "killed"}` by default. Callers that want live-ownership resolution -- the common case: `ensure_session`, routing -- get only non-terminal sessions. Callers that explicitly want history (audit/debug tooling) opt in with `include_terminal=True`.

## Renewal Call Sites

Every mutation-adjacent checkpoint in the SDLC pipeline touches the lock. This is deliberately broader than "just where the lock is first acquired" -- a single stage (e.g. BUILD) can run far longer than the gap between dispatch-time touches, so the lock must be kept alive by whichever mechanism is already firing regularly on that path.

| Call site | File | Trigger | Notes |
|---|---|---|---|
| `ensure_session()` -- all 5 return points | `tools/sdlc_session_ensure.py` | Every session resolution for an issue (cold-start create, or any of 4 early-return branches) | Wired via one shared helper (`_touch_lock_before_return`) invoked immediately before *each* `return` statement, so no branch can silently skip it. This was a round-2 critique BLOCKER: a lock-touch wired only into the bottom-of-function create path never fires for the overwhelmingly common case -- a continuing pipeline resolving via an early return. |
| `record_dispatch_for_session()` -- direct call | `tools/sdlc_dispatch.py` | Before writing every dispatch event (i.e. before every sub-skill invocation) | Calls `touch_issue_lock()` **directly**, not via `ensure_session()` -- `tools._sdlc_utils.find_session(ensure=True)`'s Step-2 short-circuit skips `ensure_session()` entirely for continuing sessions, so a call site downstream of `ensure_session()` alone would miss this path. `issue_number` is derived by parsing `session.issue_url`, not from the `issue_number` mirror field (a continuing session created before this feature shipped may not have one). Returns `False` when the lock is held elsewhere -- see "`dispatch record` merges the reason into its existing `ok: false` shape" below for what that produces at the CLI surface. |
| `decide()` peek pre-check | `tools/sdlc_next_skill.py` | Every `sdlc-tool next-skill` call, before `_resolve_enriched`/`decide_next_dispatch` run | `peek=True` -- read-only, never acquires or renews. Short-circuits to the `ISSUE_LOCKED` blocked shape ahead of all G1-G7 guard evaluation. |
| Heartbeat tier-1 (60s) block | `agent/session_executor.py::_tick_issue_lock_renewal` | Every 60s heartbeat tick, for a worker-driven session | Guarded on `agent_session.session_type == "eng"` and a resolved (truthy) `agent_session.issue_number`. Deliberately placed in the tier-1 (60s) block, not the 25-minute calendar block -- that slower cadence would blow straight past the 300s TTL. |
| `sdlc-tool stage-marker` write | `tools/sdlc_stage_marker.py` (via `tools/_sdlc_utils.py::renew_issue_lock_for_session()`) | Every stage-marker write (BUILD/TEST/REVIEW stage transitions) | Fires after the ownership guard and before the state-machine write; best-effort, never blocks or alters the write outcome on failure. |

### Deliberately excluded: `verdict record`, `meta-set`

`sdlc-tool verdict record` and `sdlc-tool meta-set` do **not** renew the lock. Both fire during PLAN/CRITIQUE-stage bookkeeping or ad hoc metadata writes with no established recurrence path through an in-progress BUILD/TEST/REVIEW stage -- renewing there would be speculative rather than load-bearing (this narrowed the scope from an earlier plan revision that wired renewal into all four `sdlc-tool` mutation subcommands, flagged as a Scope & Value CONCERN in critique). If operational experience later surfaces a concrete long-BUILD gap through one of these two, add renewal there as a follow-up rather than pre-wiring it speculatively now.

## The `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` Shape

When a lock check finds the issue owned by a different live session, the caller surfaces a blocked signal parallel to the existing G-guard `blocked` shape (`{"blocked": true, "reason": ..., "guard_id": ...}`) used elsewhere in SDLC routing.

`/sdlc` and `/do-sdlc` treat this exactly like a guard block: surface `reason` and `owner_session_id` to the human, do not loop, do not attempt to route around it by guessing an alternative skill. See `.claude/skills/sdlc/SKILL.md`'s ISSUE_LOCKED guard documentation (Step 3.5 / Step 4) for the pipeline-level interpretation contract -- not duplicated here.

### Where the literal JSON shape is actually emitted

Two call sites emit the full `{"blocked": true, ...}` shape:

- **`tools/sdlc_session_ensure.py::ensure_session()`** -- any of its five return points returns `{"blocked": True, "reason": "ISSUE_LOCKED", "owner_session_id": ...}` in place of the normal `{"session_id": ..., "created": ...}` payload when `touch_issue_lock()` reports contention.
- **`tools/sdlc_next_skill.py::decide()`** -- the peek pre-check returns `{"blocked": True, "reason": "ISSUE_LOCKED", "guard_id": "ISSUE_LOCK", "owner_session_id": ...}` before any G1-G7 guard runs.

`dispatch record` surfaces the same lock information through a different shape, described next -- it never returns `blocked: true`.

### `dispatch record` merges the reason into its existing `ok: false` shape (does not use `blocked`)

`record_dispatch_for_session()` (`tools/sdlc_dispatch.py`) intentionally stays a plain `bool` -- other call sites and existing tests already depend on that return-type contract -- so a `False` result alone is ambiguous: issue-lock contention and any other write failure (e.g. a Redis write conflict inside `update_stage_states`) both collapse to the same `False`.

The CLI wrapper (`sdlc-tool dispatch record`, implemented by `_cli_record()`) disambiguates this after the fact: on a failed write (`ok: false`), it calls `_peek_issue_lock_conflict()` -- a read-only, non-mutating `touch_issue_lock(peek=True)` check that re-derives the issue number from `session.issue_url` the same way `record_dispatch_for_session()` does internally. If the peek shows the lock held by a different live session, `reason` and `owner_session_id` are merged into the result dict alongside the pre-existing keys:

```json
{"ok": false, "history_length": 3, "reason": "ISSUE_LOCKED", "owner_session_id": "sdlc-local-1954"}
```

A write failure unrelated to the lock (or a session with no parseable issue number) keeps the original `{"ok": false, "history_length": N}` shape unchanged -- this is purely additive, not a new top-level contract. A successful write (`ok: true`) never triggers the peek at all.

`/sdlc` and `/do-sdlc` treat either shape (`blocked: true` from `ensure_session()`/`next-skill`, or `ok: false` + `reason: "ISSUE_LOCKED"` from `dispatch record`) identically: surface `reason` and `owner_session_id` to the human, do not loop, do not attempt to route around it by guessing an alternative skill.

## Interaction with Crash Recovery

A revived terminal session (via `reflections/crash_recovery.py`'s auto-resume or a manual `valor-session resume`) that finds its issue already owned by a live peer steps aside at the same checkpoints described above -- `ensure_session()` and `record_dispatch_for_session()` both run before any real BUILD-or-later work happens on the revived session's next turn. No separate pre-revival gate was added inside `crash_recovery.py` itself; the plan's Open Questions Resolution concluded the existing checkpoints already cover the revival path without needing a third gate. See [Session Recovery Mechanisms](session-recovery-mechanisms.md) for the full recovery-mechanism catalogue and the corresponding race-condition entry.

## Source Files

| File | Role |
|---|---|
| `models/session_lifecycle.py` | `touch_issue_lock()`, `_process_holder_token()`, `IssueLockResult`, `ISSUE_LOCK_TTL_SECONDS` |
| `models/agent_session.py` | `issue_number` mirror field |
| `tools/_sdlc_utils.py` | `find_session_by_issue(include_terminal=...)`, `renew_issue_lock_for_session()` |
| `tools/sdlc_session_ensure.py` | `ensure_session()`'s 5 return-point wiring |
| `tools/sdlc_dispatch.py` | `record_dispatch_for_session()`'s direct lock call; `_peek_issue_lock_conflict()` + `_cli_record()`'s post-failure disambiguation |
| `tools/sdlc_next_skill.py` | `decide()`'s peek pre-check |
| `agent/session_executor.py` | `_tick_issue_lock_renewal()` (tier-1 heartbeat) |
| `tools/sdlc_stage_marker.py` | `write_marker()`'s renewal call |
| `.claude/skills/sdlc/SKILL.md` | `ISSUE_LOCKED` guard interpretation contract for `/sdlc`/`/do-sdlc` |
| `.claude/skills-global/do-sdlc/SKILL.md` | Mints + threads the `SDLC_HOLDER_TOKEN` run token across the supervisor's `sdlc-tool` subprocesses (issue #1971) |
