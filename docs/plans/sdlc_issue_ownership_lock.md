---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1954
last_comment_id: 4912565005
revision_applied: true
---

# SDLC issue ownership lock

## Problem

Two independent SDLC entry points — a local Claude Code CLI session driving `/do-sdlc`/`/sdlc`, and the standalone `worker` process driving `AgentSession` records through the headless PM+dev runner — can both start and drive pipeline work on the same GitHub issue concurrently. Neither entry point can see the other. This isn't hypothetical: it happened on issue #1915. A worker-driven `sdlc-local-1915` session failed, was revived (crash-recovery or manual resume), and picked back up by the worker's normal pickup loop — while, independently, a local CLI session had begun driving the same issue in a different worktree. The worker-driven session finished first and merged PR #1946. The local CLI session had no way to learn this, kept driving its stale worktree through BUILD-stage dispatches, and eventually opened a second, substantively duplicate PR (#1952), which had to be manually identified, closed, and cleaned up.

**Current behavior:**
- No issue-level lock/claim/owner concept exists anywhere in the codebase (`models/agent_session.py` has no `claim`/`lock`/`owner` fields or helpers).
- `find_session_by_issue()` (`tools/_sdlc_utils.py:91-180`) has no status filter — it can and does return a terminal (`failed`/`completed`/`killed`) session as "the" session for an issue, which is part of how the #1915 incident happened (the terminal `sdlc-local-1915` record was revived and picked up while a second, independent session already believed it owned the issue).
- `agent/sdlc_router.py::decide_next_dispatch()` and `tools/sdlc_dispatch.py::record_dispatch_for_session()` are pure — they inspect only their own session's `stage_states`/`meta` and never check whether a different session is already working the same issue.

**Desired outcome:** When a second session (local-CLI or worker-driven) attempts to start or continue pipeline work on a GitHub issue that another live session already owns, it detects this before doing BUILD-stage (or later) work — ideally before any stage dispatch — and steps aside instead of duplicating the work. A session whose owner has genuinely died or gone stale must not permanently block the issue.

## Freshness Check

**Baseline commit:** e767eff2
**Issue filed at:** 2026-07-08T07:43:06Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/agent_session.py:386-389` — recovery-attempt finalize-to-`failed` comment — still holds, no drift.
- `tools/_sdlc_utils.py:91-180` (`find_session_by_issue`) — still holds exactly as described: issue_url pass, then deterministic-id pass, then message_text pass, with no status filter anywhere in the function. Confirmed by direct read.
- `tools/sdlc_dispatch.py:45-90` (`record_dispatch_for_session`) — still holds: wraps `agent.sdlc_router.record_dispatch` + `tools.stage_states_helpers.update_stage_states`, no cross-session awareness.
- `models/session_lifecycle.py:705-763` (`claim_pending_run` + its rationale comment) — still holds verbatim; this is the closest working precedent for the new issue-level lock.
- `models/agent_session.py` — confirmed no `issue_number` field exists; only `issue_url` (plain `Field`, unindexed) and `slug` (`KeyField`, indexed).

**Cited sibling issues/PRs re-checked:**
- #1817 — closed 2026-07-02. Its B2 finding (non-atomic pending→running claim) is fixed by `claim_pending_run()`; confirmed still present and unmodified since.
- #1915 — closed 2026-07-08T06:17:39Z, merged as PR #1946 (merged 2026-07-08T06:17:38Z, "Fix SDLC fork: slug-wins worktree/branch ownership + live-ref PR dedup guard"). The duplicate PR #1952 it produced was closed 2026-07-08T07:33:21Z — both events predate this issue's filing and are already reflected in its Prior Context.

**Commits on main since issue was filed (touching referenced files):** none — `git log --since=<filed-at>` on `models/agent_session.py`, `models/session_lifecycle.py`, `tools/_sdlc_utils.py`, `tools/sdlc_dispatch.py`, `agent/sdlc_router.py` returns no results.

**Active plans in `docs/plans/` overlapping this area:** none found (`grep -rl "1954\|issue_lock\|issue ownership" docs/plans/` returns nothing pre-existing).

**Notes:** No drift. The issue's Recon Summary is accurate as of this plan's baseline; proceeding on the premises as filed.

## Prior Art

- **#1817** (closed, merged as part of PR #1875/other work): introduced `claim_pending_run()` — a standalone Redis `SET NX EX` key (`session:runclaim:{session_id}`) gating the `pending`→`running` transition, explicitly documented as an *additive* gate that does not replace the Popoto-level optimistic-concurrency CAS in `transition_status()`. This is the direct precedent this plan generalizes from single-session-lifecycle scope to issue-level scope.
- **#1374** (closed, not merged as described): proposed a two-axis `claim_state`/run-attempt-phase refactor of `AgentSession.status` inspired by OpenAI Symphony, plus a new `RunAttempt` model. Broader in scope than this issue (it targets the *session's own* lifecycle ambiguity, not cross-session issue ownership) and was closed without landing that redesign — not directly reusable, but confirms the repo has already considered (and shelved) a heavier claim-state model. This plan intentionally stays narrower: an issue-level lock layered on top of the existing status model, not a status-model rewrite.
- **#875** (closed, foundational): established `session_lifecycle.py` as the CAS-protected status authority. The issue-level lock this plan adds is a peer mechanism alongside that CAS, following the same "additive gate, not a replacement" pattern `claim_pending_run` set.
- **#1272** (closed): guarded against parallel-session *main-checkout* contamination (worktree isolation), a related but distinct failure mode (filesystem collision, not issue-ownership collision). Already addressed by worktree/slug conventions; not a duplicate of this work.

No prior attempt targeted issue-level cross-entry-point ownership directly — this is a genuinely new gap, not a re-solve.

## Research

No relevant external findings — proceeding with codebase context and training data. The problem (distributed mutual exclusion with liveness/staleness detection) is well-covered internally by the existing `SET NX EX` idiom used a dozen+ places in this codebase; no external library or pattern research changes the approach.

## Data Flow

1. **Entry point A (local CLI):** `/do-sdlc` or `/sdlc` invokes `sdlc-tool` subcommands, which call `ensure_session()` (`tools/sdlc_session_ensure.py`) to resolve or create the tracking `AgentSession` (`sdlc-local-{N}`), then `next-skill`/`dispatch record` (`agent/sdlc_router.py`, `tools/sdlc_dispatch.py`) before each sub-skill invocation.
2. **Entry point B (worker):** `agent/session_pickup.py` pops a `pending` `AgentSession` (possibly revived by `reflections/crash_recovery.py::run_crash_recovery()` or `tools/valor_session.py resume`), claims it via `claim_pending_run()`, then drives it through the headless PM+dev runner (`agent/session_runner/`), which itself calls into the same `sdlc-tool`/`agent/sdlc_router.py` machinery for pipeline routing during PM turns.
3. **Shared decision point:** both entry points converge on `record_dispatch_for_session()` (`tools/sdlc_dispatch.py:45-90`) immediately before every sub-skill dispatch, and on `ensure_session()`/`find_session_by_issue()` (`tools/_sdlc_utils.py`, `tools/sdlc_session_ensure.py`) whenever a session for an issue number is resolved.
4. **Output:** currently, both paths independently reach BUILD/MERGE with no cross-check — this plan inserts an atomic ownership check at the shared decision points in step 3, before either path proceeds to dispatch.

## Solution

### Key Elements

- **Issue-level lock primitive**: a new function alongside `claim_pending_run()` in `models/session_lifecycle.py` — `touch_issue_lock(issue_number, holder_token, ttl)` — backed by a standalone Redis key (`session:issuelock:{issue_number}`, `SET NX EX` / conditional `EXPIRE` renewal), matching the codebase's established claim idiom rather than a field-based CAS on the Popoto model.
- **Holder identity is a per-process token, NOT `session_id` (round-2 critique BLOCKER)**: both entry points resolve the *same* deterministic `session_id` for a given issue — local CLI and worker both compute `sdlc-local-{issue_number}` (confirmed: `tools/sdlc_session_ensure.py::ensure_session()` always derives `local_session_id = f"sdlc-local-{issue_number}"`, with no per-process or per-machine differentiator). If `touch_issue_lock` compared identity by `session_id`, two independently-live processes racing the same issue would each pass `"sdlc-local-{N}"` and each would see the lock as "already held by me" — the exact collision this plan exists to prevent. The lock value is therefore keyed on a **process-unique `holder_token`**: `uuid.uuid4().hex` generated once per process and cached in a module-level variable (`_PROCESS_HOLDER_TOKEN` in `models/session_lifecycle.py`, lazily initialized on first call, stable for the life of that OS process). Two processes handling the same `session_id` on the same or different machines get distinct tokens; the same process calling repeatedly (e.g. dispatch → heartbeat → dispatch) reuses its own token and renews cleanly. The Redis value stored is a small JSON payload `{"holder_token": "...", "session_id": "...", "pid": <int>, "hostname": "..."}` — the token is the sole field compared for ownership; `session_id`/`pid`/`hostname` are carried along purely so a human-readable owner can be surfaced in the blocked-dispatch message (`owner_session_id` in the response is read from this payload's `session_id` field, not derived from the token).
- **Visibility mirror field**: a new `issue_number` field (plain `IntField`, unindexed) on `AgentSession`, written **once, at session creation** (parsed from `issue_url` at the point `ensure_session()` creates the record) — not re-written on every renewal call, since the value is invariant for a session's lifetime (critique CONCERN — Scope & Value: renewing it repeatedly would be redundant writes with no behavioral payoff). This satisfies the repo owner's stated preference that the lock be visible via `valor_session inspect`/`sdlc-tool stage-query` — but the field is purely a read-side mirror. It never gates the decision; only the Redis key does. This resolves the tension flagged in the issue's Solution Sketch: the atomic correctness lives in the established Redis-key idiom (matching `claim_pending_run` and a dozen other call sites), while the requested inspectability lives on the model.
- **Renewal at every mutation point**: `touch_issue_lock()` is called from `ensure_session()` (all four of its return points, not just cold-start — see round-2 critique fix below), directly from the `sdlc-tool` mutation-subcommand handlers (see below — not indirectly through `ensure_session()`, which the continuing-session path bypasses), and from the worker's existing per-turn heartbeat loop (`agent/session_executor.py::_heartbeat_loop`, already ticking every `HEARTBEAT_WRITE_INTERVAL`=60s) for any eng session with a resolved issue number. The heartbeat piggyback is required because a single stage (e.g. BUILD) can run far longer than the interval between dispatch-time touches, and the lock must not expire mid-stage on the owning session.
- **`ensure_session()`'s 4 early-return points all need the lock touch (round-2 critique BLOCKER)**: `tools/sdlc_session_ensure.py::ensure_session()` has four distinct `return {...}` statements that fire *before* the bottom-of-function creation path — (1) the env short-circuit's "env session owns the issue" return, (2) the env short-circuit's "prefer existing issue-scoped session" return, (3) the `find_session_by_issue(issue_number)` match return, (4) the idempotent `existing_by_id` match return — plus (5) the final create-and-claim path at the bottom. A lock-touch call wired only into the bottom-of-function create path (as the round-1 revision implicitly assumed) never fires for the overwhelmingly common case: a continuing pipeline calling `ensure_session()` again resolves via one of returns (1)-(4) and exits without ever touching the lock. Fix: extract the `touch_issue_lock(issue_number, ttl=...)` call into a single local helper invoked immediately before *each* of the five `return` statements, not appended once at the end.
- **Local-CLI renewal, scoped to BUILD-or-later-relevant call sites only**: local `/do-sdlc` sessions don't go through the worker's heartbeat loop. Renewal is wired into `stage-marker` and `dispatch record` — the two subcommands that fire immediately before/around a stage transition and therefore correspond to actual BUILD-or-later progression checkpoints. `verdict record` and `meta-set` are **not** wired for renewal: they fire during PLAN/CRITIQUE-stage bookkeeping or ad hoc metadata writes with no established recurrence path through an in-progress BUILD/TEST/REVIEW stage, so renewing there would be speculative rather than load-bearing (per critique CONCERN — Scope & Value). If operational experience later surfaces a concrete long-BUILD gap through one of these two, add renewal there as a follow-up rather than pre-wiring it now.
- **`next-skill`'s missing code path (critique BLOCKER)**: `tools/sdlc_next_skill.py::decide()` resolves state via read-only `query_enriched()` and calls the unmodified, pure `decide_next_dispatch(stage_states, meta, context)` — there is no existing hook point for an `ISSUE_LOCKED` block. Fix: add a `touch_issue_lock(issue_number, session_id, peek=True)` pre-check at the very top of `decide()`, before `_resolve_enriched`/`decide_next_dispatch` run. `peek=True` means: check-only, no acquire/renew side effect (a `next-skill` call must not itself claim or extend a lock — only mutation subcommands do that). If the peek finds the lock held by a different live session, `decide()` short-circuits to `{"blocked": True, "reason": "ISSUE_LOCKED", "owner_session_id": ...}` before any guard evaluation. This scopes the previously-unbacked "next-skill surfaces ISSUE_LOCKED" claim to a concrete, buildable code path instead of dropping it.
- **`find_session(ensure=True)`'s short-circuit (critique BLOCKER)**: `tools/_sdlc_utils.py::find_session(ensure=True)` — the resolver all four `sdlc-tool` mutation subcommands use — returns immediately at its Step 2 (`find_session_by_issue()` match) for any existing session, terminal or not, **without calling `ensure_session()`**. Since continuing an in-flight pipeline session is the common case, `ensure_session()` is only reached on true cold-start. Each CLI subcommand handler (`sdlc_dispatch._cli_record`, `sdlc_stage_marker`'s handler) therefore calls `touch_issue_lock()` directly using the resolved session, independent of whether `find_session()` short-circuited at Step 2 or reached `ensure_session()` at Step 4. `issue_number` for this direct call is derived by parsing `session.issue_url` (mirroring `find_session_by_issue`'s existing `target_suffix` parsing pattern) rather than assuming an `ensure_session()` call preceded it.
- **Fail-open on infra errors, fail-closed on contention**: mirroring `claim_pending_run`, a Redis error is logged and treated as "proceed" (never block progress on a Redis hiccup). Genuine contention (another session holds a live, unexpired lock) is a hard stop — the caller must not dispatch.
- **`find_session_by_issue()` status filter**: add an explicit `include_terminal: bool = False` parameter (default `False`). Callers that want live-ownership resolution (the common case — `ensure_session`, routing) get only non-terminal sessions; callers that explicitly want history (e.g. audit/debug tooling) opt in with `include_terminal=True`.
- **Blocked-dispatch signal**: `sdlc-tool next-skill`/`dispatch record` surface a new `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_session_id": "..."}` shape (parallel to the existing G-guard `blocked` shape) when the lock check fails. `/sdlc` and `/do-sdlc` treat this exactly like a guard block: surface to the human, do not loop, do not guess.

### Flow

Local CLI or worker resolves a session for issue N → `ensure_session()`/`record_dispatch_for_session()` calls `touch_issue_lock(N, session_id)` → **lock free or already owned by this session** → renew TTL, mirror `issue_number` onto the session, proceed to dispatch. **Lock held by a different, live session** → return `blocked` with the owner's session id → caller surfaces "issue #N is already being worked by session {owner}" and stops, instead of dispatching a duplicate BUILD.

### Technical Approach

- `ISSUE_LOCK_TTL_SECONDS` (env-overridable, provisional default 300s = 5x the 60s heartbeat interval, grain of salt / tunable) in `models/session_lifecycle.py`, next to `RUN_CLAIM_TTL_SECONDS`.
- `touch_issue_lock(issue_number, session_id, ttl=ISSUE_LOCK_TTL_SECONDS, peek=False) -> IssueLockResult` (a small `NamedTuple`/dataclass: `acquired: bool`, `owner_session_id: str | None`). `session_id` is accepted for the human-readable payload only; ownership comparison is done against this process's cached `holder_token` (see Key Elements), not `session_id`. Implementation: build `value = json.dumps({"holder_token": _process_holder_token(), "session_id": session_id, "pid": os.getpid(), "hostname": socket.gethostname()})`; `SET key value NX EX ttl`; on failure, `GET key` and parse JSON — if `holder_token` matches this process's token, `EXPIRE key ttl` (renew, still ours); if it differs, return not-acquired with the current owner's `session_id` from the parsed payload (staleness is handled implicitly by TTL expiry, no separate PID probe needed — this mirrors why `claim_pending_run` doesn't need one either). A malformed/legacy (non-JSON) existing value is treated as a foreign, non-matching holder (fail toward "not acquired", never crash on `json.loads`).
- `_process_holder_token() -> str`: lazily initializes and caches `uuid.uuid4().hex` in a module-level variable in `models/session_lifecycle.py` on first call; stable for the process lifetime, distinct across processes (including two processes that happen to resolve the same `session_id`).
- Fails open (`acquired=True`) on any Redis exception, logged at `warning`, exactly like `claim_pending_run`.
- Wire `touch_issue_lock` calls into:
  - `tools/sdlc_session_ensure.py::ensure_session()` — at **all five** return points (the 4 early-return branches plus the final create-and-claim path), not only cold-start creation. Extract into one local helper called immediately before each `return`, so no branch can silently skip the lock touch.
  - `tools/sdlc_dispatch.py::record_dispatch_for_session()` (before writing the dispatch event), called **directly** rather than assumed to run only after `ensure_session()` — see the `find_session(ensure=True)` short-circuit fix above. `issue_number` here is parsed from `session.issue_url`, not passed down from an `ensure_session()` call that may not have run.
  - `tools/sdlc_next_skill.py::decide()` (peek-only, no acquire/renew) — before `_resolve_enriched`/`decide_next_dispatch` run.
  - `agent/session_executor.py::_heartbeat_loop`, specifically in the **tier-1 (60s) block** alongside `session.last_heartbeat_at` — not the 25-minute calendar block, which would blow past the 300s TTL. Both `session.session_type` and `agent_session.issue_number` are already in scope at that point in the function (confirmed by reading `agent/session_executor.py:1945-1983`); guard on `agent_session and agent_session.session_type == "eng" and agent_session.issue_number`.
  - The `sdlc-tool` CLI mutation subcommand handlers for `stage-marker` and `dispatch record` only (not `verdict record`/`meta-set` — see the narrowed renewal-scope bullet above), via one shared helper so there's a single call site implementation reused by both, not two copy-pasted ones.
- `find_session_by_issue()` signature becomes `find_session_by_issue(issue_number: int, include_terminal: bool = False)`; the three existing passes (issue_url, deterministic-id, message_text) each filter out sessions whose `status` is in `{"failed", "completed", "killed"}` unless `include_terminal=True`. Audit every existing call site of `find_session_by_issue` to confirm none silently depended on terminal-session matching (expected: none do, since the incident is precisely that this was a bug).
- New `blocked` shape from `next-skill`/`dispatch record`: extend the existing guard-block JSON contract (`{"blocked": true, "reason": ..., "guard_id": ...}`) with a sibling `reason="ISSUE_LOCKED"` case; `guard_id` omitted or set to a new sentinel like `"ISSUE_LOCK"` so `/sdlc`'s Step 4 interpretation logic (which already branches on `blocked`) needs no structural change, only a new reason string to surface verbatim to the human. For `next-skill` specifically, this shape is emitted by the new peek pre-check in `decide()` described above — not by `decide_next_dispatch()` itself.
- No changes to `agent/sdlc_router.py::decide_next_dispatch()`'s guard table (G1-G7) — the issue lock is a pre-check ahead of routing, not a new G-guard, since it answers "is this session even allowed to act on this issue right now," a different question from "given this session's own state, what's the next stage."

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `touch_issue_lock()`'s Redis-error catch (fail-open) needs a test asserting: (a) a simulated Redis exception logs a warning and returns `acquired=True`, (b) does not raise.
- No other new exception handlers are introduced by this plan beyond the one above.

### Empty/Invalid Input Handling
- [ ] `touch_issue_lock(issue_number=0 or None, ...)` — document and test the no-op/fail-open behavior (mirrors `claim_pending_run`'s `if not session_id` guards where applicable).
- [ ] `find_session_by_issue(issue_number, include_terminal=False)` with no matching sessions at all — must return `None`, not raise.

### Error State Rendering
- [ ] The `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` shape from `sdlc-tool next-skill` must be verified to reach the human-visible surface (Telegram reply / CLI stdout) as readable prose, not a raw JSON dump — test via the same path the existing G4 oscillation `blocked` reason already uses, since it shares the same rendering code.

## Test Impact

- [ ] `tests/unit/test_sdlc_session_ensure.py` (or equivalent) — UPDATE: add cases asserting `ensure_session()` calls `touch_issue_lock` from each of its five return points (not just the create path), and that two calls simulating distinct processes (distinct `holder_token`s) resolving the *same* `sdlc-local-{N}` session_id correctly detect contention rather than both succeeding.
- [ ] `tests/unit/test_sdlc_dispatch.py` — UPDATE: `record_dispatch_for_session()` must refuse to record + return `False` (or a blocked signal) when the issue lock is held by a different session.
- [ ] Any existing test asserting `find_session_by_issue()` matches a terminal (`failed`/`completed`/`killed`) session as "the" owner — UPDATE: change expectation to `None` (or to the new `include_terminal=True` call) since that behavior was the bug.
- [ ] `tests/unit/test_session_lifecycle.py` (`claim_pending_run` tests) — UPDATE: add sibling test cases for the new `touch_issue_lock`, following the same test structure (mock Redis `SET NX EX`, assert acquire/renew/reject/fail-open paths).
- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: add a case where `next-skill` returns the new `ISSUE_LOCKED` blocked shape ahead of normal guard evaluation, and confirm normal G1-G7 guard behavior is unaffected when no other session holds the lock.
- [ ] `tests/unit/test_session_executor_tick_backstop.py` — UPDATE (corrected from a nonexistent `test_session_executor.py` per critique structural finding): add a case asserting the tier-1 (60s) heartbeat tick calls `touch_issue_lock()` for an `eng` session with a resolved `issue_number`, and does not call it from the 25-minute calendar block.

## Rabbit Holes

- Building a full distributed-lock library (fencing tokens, lease renewal daemons, quorum) — the existing `SET NX EX` + TTL-expiry idiom is sufficient and is what every other lock in this codebase already uses; do not over-engineer.
- Reviving or adopting #1374's two-axis `claim_state`/`RunAttempt` redesign as a prerequisite — that issue was closed without landing and is a much larger, orthogonal refactor of session lifecycle itself, not the issue-ownership gap this plan fixes.
- Adding a PID-liveness probe for the *other* session as a supplement to TTL expiry — unnecessary complexity; TTL expiry already answers "is the owner still active enough to be renewing" without needing to reach into another process's PID namespace (which may be on a different machine entirely — sessions are not guaranteed to be co-located).
- Reworking `agent/sdlc_router.py`'s G1-G7 guard table to fold the issue lock in as an 8th guard — keep it as a separate pre-check layer; the guards answer "given this session's own state, what's next," the lock answers "is this session allowed to act on this issue at all right now." Conflating them complicates both.

## Risks

### Risk 1: TTL too short causes false lock-loss on a legitimately slow session
**Impact:** A session doing a genuinely long BUILD or review with sparse `sdlc-tool` calls could have its lock expire and get pre-empted by a second session, recreating a milder version of the original bug.
**Mitigation:** Renewal is wired into the worker's existing 60s heartbeat loop (independent of dispatch cadence) for worker-driven sessions, and into every `sdlc-tool` state-mutation subcommand for local CLI sessions — both fire far more often than the 5-minute TTL, giving generous margin. `ISSUE_LOCK_TTL_SECONDS` is env-overridable if operational experience shows it's too tight.

### Risk 2: Two sessions racing the very first `SET NX EX` call
**Impact:** If both `touch_issue_lock()` calls hit Redis in the same instant, only one can win the `NX` — but if the check happens *after* one session has already done non-trivial work assuming ownership, work could still be wasted.
**Mitigation:** The check happens at `ensure_session()` (before any dispatch) and again at `record_dispatch_for_session()` (before every subsequent sub-skill), so the window for wasted work is bounded to "between two dispatch-adjacent checkpoints," not "the whole pipeline," which is the core improvement over the current zero-checks state.

### Risk 3: Existing callers of `find_session_by_issue()` implicitly relied on terminal-session matching
**Impact:** Changing the default to exclude terminal sessions could silently change behavior for a caller that expected to find a `failed`/`completed` session (e.g., some inspection/reporting tool).
**Mitigation:** Grep all call sites of `find_session_by_issue` during BUILD and audit each; anything needing terminal visibility passes `include_terminal=True` explicitly rather than relying on the (buggy) default.

## Race Conditions

### Race 1: Concurrent `ensure_session()` calls for the same issue from both entry points
**Location:** `tools/sdlc_session_ensure.py::ensure_session()`, `models/session_lifecycle.py::touch_issue_lock()` (new)
**Trigger:** Local CLI and worker both resolve a session for the same issue number within the same instant (e.g., a crash-recovery revive fires just as a human starts `/do-sdlc` manually).
**Data prerequisite:** The Redis key `session:issuelock:{issue_number}` must not exist yet, or must be expired.
**State prerequisite:** Both callers must go through `touch_issue_lock()` before proceeding to any dispatch — no code path may bypass it.
**Mitigation:** `SET NX EX` is atomic at the Redis level; exactly one caller wins on the first `SET`. The loser's `GET`-and-compare path checks the winner's `holder_token`, not `session_id` — critical here since both callers may well resolve the identical `sdlc-local-{N}` session_id (see round-2 critique fix in Key Elements). The loser receives `acquired=False` + the winner's `owner_session_id` (read from the stored payload's `session_id` field, for human display only) and must surface a blocked/stand-aside signal rather than proceeding.

### Race 2: Lock expiry exactly during a live session's slow stage
**Location:** `agent/session_executor.py::_heartbeat_loop`, `models/session_lifecycle.py::touch_issue_lock()`
**Trigger:** A worker-driven session's heartbeat write is delayed (e.g., Redis latency spike) right as the TTL boundary passes, and a second session's `ensure_session()` call lands in that exact gap.
**Data prerequisite:** The second session's claim attempt must occur after the key has actually expired in Redis (not just "logically due").
**State prerequisite:** None beyond Redis's own TTL guarantees.
**Mitigation:** This is an accepted, bounded risk inherent to any TTL-based lock (same as `claim_pending_run`'s existing 30s window) — the mitigation is generous TTL sizing relative to renewal cadence (5 min TTL vs. 60s heartbeat), not eliminating the window entirely.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1374] Adopting the full `claim_state`/`RunAttempt` two-axis session lifecycle redesign — that issue is closed without landing and is an orthogonal, much larger refactor of session lifecycle bookkeeping, not required to fix issue-level ownership collisions.
- [EXTERNAL] Confirming with certainty which exact mechanism (crash_recovery.py auto-resume vs. manual `valor-session resume`) revived `sdlc-local-1915` in the original incident — the issue itself notes this wasn't confirmed during triage; this plan's fix covers both paths regardless (both go through `ensure_session()`/pickup, both get the same lock check), so root-causing the exact revival trigger is not required to close this gap.
- [DESTRUCTIVE] Migrating/backfilling `issue_number` onto existing historical `AgentSession` records — the new field only needs to be populated going forward on new lock acquisitions; backfilling historical rows for a purely-visibility field on records that are largely terminal/archived is not worth the migration risk.
- [SEPARATE ISSUE] The worker-startup-recovery liveness gap reported against issue #1920 (`_recover_interrupted_agent_sessions_startup` classifying a live, single-`AgentSession` eng session as orphaned purely on wall-clock age, without checking `claude_pid` or `last_sdk_heartbeat_at` the way `_sweep_dead_worker_sessions` already does) is a distinct mechanism that produces the same symptom (duplicate concurrent pipeline work) but requires **zero** second session and **zero** issue-level lock contention — a single revived session replays its own triggering message. This issue's lock does not close that gap (there's only ever one session/lock-holder in that scenario). Filing a follow-up issue for the liveness-gate fix in `agent/session_health.py` is out of scope here; this plan's regression coverage stays scoped to genuine multi-session issue contention.

## Update System

No update system changes required — this feature is purely internal to the SDLC pipeline's session-tracking and routing code (`models/`, `tools/`, `agent/`). It adds no new dependencies, config files, or deployment steps. The one schema addition (`issue_number` field on `AgentSession`) requires a Popoto migration per this repo's convention (see below), which is a one-time `scripts/update/migrations.py` entry, not an update-process change.

**Popoto schema migration**: add a migration function to `scripts/update/migrations.py`, register it in the `MIGRATIONS` dict. Since `issue_number` is `null=True` with no backfill required (see No-Gos), the migration only needs to confirm the field is readable via the existing backcompat-descriptor healing path (`_heal_descriptor_pollution`, per memory `feedback_field_backcompat_heal.md`) — no explicit backfill logic needed.

## Agent Integration

No new MCP server or `.mcp.json` changes required. The `sdlc-tool` CLI (already an existing agent-facing surface, invoked via Bash by `/sdlc`/`/do-sdlc`) gains: the new `ISSUE_LOCKED` blocked-reason shape from `next-skill` and `dispatch record`, and lock-renewal as a side effect of `stage-marker`/`verdict record`/`meta-set`. These are additive changes to an existing CLI surface, not a new entry point. `/sdlc`'s SKILL.md Step 4 interpretation logic already branches on `{"blocked": true, ...}` — it needs one line added acknowledging the new `reason` value, covered in this plan's Step by Step Tasks.

- Integration test: a test invoking `sdlc-tool next-skill --issue-number N` twice from two independently-resolved sessions for the same issue confirms the second call returns the blocked shape.

## Documentation

- [ ] Create `docs/features/sdlc-issue-ownership-lock.md` describing: the `touch_issue_lock` primitive, the `issue_number` mirror field, the renewal call sites, the `include_terminal` parameter on `find_session_by_issue`, and the `ISSUE_LOCKED` blocked-dispatch shape.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/session-recovery-mechanisms.md` to note that crash-recovery revival of a terminal session is now also gated by the issue lock (a revived session that finds the issue already owned by a live peer steps aside rather than racing it).
- [ ] Update `.claude/skills/sdlc/SKILL.md` (or its `docs/sdlc/` addendum) to document the `ISSUE_LOCKED` blocked reason alongside the existing G1-G7 guard table, since it's evaluated at a similar decision point even though it isn't formally a "G-guard."

## Success Criteria

- [x] Two independently-resolved sessions (simulated: one via `ensure_session()` direct call, one via a second `ensure_session()` call for the same issue number, **each with its own `holder_token`** — the realistic two-process scenario, since both resolve the identical `sdlc-local-{N}` session_id) — only one acquires the issue lock; the second receives a blocked signal with the first's session id.
- [x] Calling `ensure_session()` a second time *from the same process* for the same issue (same `holder_token`) is treated as a renewal, not contention — confirms the fix doesn't lock out a session's own subsequent calls.
- [x] `find_session_by_issue(N)` no longer returns a `failed`/`completed`/`killed` session by default; `find_session_by_issue(N, include_terminal=True)` still does.
- [x] `record_dispatch_for_session()` refuses to dispatch when the issue lock is held by a different live session.
- [x] A session whose lock has expired (simulated via a short TTL in test) no longer blocks a second session from claiming the issue.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)
- [x] `grep -rn "touch_issue_lock" tools/ agent/ models/` confirms all call sites are wired: `ensure_session()`, `record_dispatch_for_session()` (direct call, not via `ensure_session()`), `tools/sdlc_next_skill.py::decide()` (peek), the heartbeat loop's tier-1 block, and the `stage-marker`/`dispatch record` CLI subcommands.
- [x] `sdlc-tool next-skill --issue-number N` returns `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` when a second session holds the lock — confirming the peek pre-check in `decide()` actually short-circuits (this is the concrete fix for critique BLOCKER 1; without it there is no code path producing this shape).

## Team Orchestration

### Team Members

Collapsed from four named agents to two (critique NIT — Scope & Value: a Medium-appetite bug fix built around one lock primitive plus a handful of call sites doesn't need four-way process scaffolding without a stated parallelism need):

- **Builder (core)**
  - Name: core-builder
  - Role: Implement `touch_issue_lock()` in `models/session_lifecycle.py`, the `issue_number` field + migration, the `find_session_by_issue` status filter, and wire `touch_issue_lock` into `ensure_session()`, `record_dispatch_for_session()`, `tools/sdlc_next_skill.py::decide()` (peek), the worker heartbeat loop's tier-1 block, and the `stage-marker`/`dispatch record` CLI subcommands; add the `ISSUE_LOCKED` blocked shape to `next-skill`/`dispatch record`; update `.claude/skills/sdlc/SKILL.md`
  - Agent Type: builder
  - Resume: true

- **Validator + Documentarian (integration and docs)**
  - Name: lock-validator-docs
  - Role: Verify the two-session race scenario end-to-end, confirm `/sdlc` surfaces the blocked reason correctly, confirm no regression to existing G1-G7 guard behavior, then write `docs/features/sdlc-issue-ownership-lock.md` and update the cross-referenced docs listed above
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement the issue-lock primitive
- **Task ID**: build-lock-primitive
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle.py` (new `touch_issue_lock` cases)
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `ISSUE_LOCK_TTL_SECONDS` (env-overridable, provisional default 300) next to `RUN_CLAIM_TTL_SECONDS` in `models/session_lifecycle.py`
- Implement `_process_holder_token()` (lazy-cached `uuid.uuid4().hex`, module-level, one per process) in `models/session_lifecycle.py`
- Implement `touch_issue_lock(issue_number, session_id, ttl=ISSUE_LOCK_TTL_SECONDS, peek=False) -> IssueLockResult`, storing a JSON payload (`holder_token`, `session_id`, `pid`, `hostname`) and comparing ownership by `holder_token` (never by `session_id`, since two live processes can share the same deterministic `session_id`); `peek=True` reads current lock state without acquiring/renewing (used by `next-skill`)
- Add `issue_number = IntField(null=True)` to `AgentSession` in `models/agent_session.py`, written once at session creation (parsed from `issue_url`), never re-written on renewal
- Add a migration entry to `scripts/update/migrations.py` / `MIGRATIONS` dict (no backfill needed, per No-Gos)
- Write unit tests: acquire, renew-by-owner (same process/token), reject-by-different-process (same `session_id`, different `holder_token` — the round-2 regression case), reject-by-non-owner, fail-open on Redis exception, malformed/legacy lock value treated as foreign, TTL expiry re-claim, peek-only (no side effect)

### 2. Fix `find_session_by_issue()` status filter
- **Task ID**: build-status-filter
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_utils.py` (or equivalent existing test file for `_sdlc_utils.py`)
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `include_terminal: bool = False` parameter to `find_session_by_issue()`
- Filter all three passes (issue_url, deterministic-id, message_text) to exclude `status in {"failed", "completed", "killed"}` unless `include_terminal=True`
- Grep all existing call sites of `find_session_by_issue`; update any that need `include_terminal=True` explicitly
- Update/add tests asserting terminal sessions are excluded by default and included when requested

### 3. Wire lock checks into ensure_session, dispatch recording, and next-skill
- **Task ID**: build-wire-checkpoints
- **Depends On**: build-lock-primitive
- **Validates**: `tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_sdlc_dispatch.py`, `tests/unit/test_sdlc_router.py`
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- Call `touch_issue_lock()` from `ensure_session()` (`tools/sdlc_session_ensure.py`) at all five return points — the 4 early-return branches (env-owns-issue, env-diverges-but-issue-owned, `find_session_by_issue` match, idempotent `existing_by_id` match) plus the final create-and-claim path — via one shared local helper so no branch can skip it; write `issue_number` onto the session once, on creation only
- Call `touch_issue_lock()` **directly** from `record_dispatch_for_session()` (`tools/sdlc_dispatch.py`) before writing the dispatch event, deriving `issue_number` by parsing `session.issue_url` — do not assume `ensure_session()` ran first, since `find_session(ensure=True)`'s Step-2 short-circuit skips it for continuing sessions; return `False`/blocked when not acquired
- Add a `touch_issue_lock(peek=True)` pre-check at the top of `tools/sdlc_next_skill.py::decide()`, before `_resolve_enriched`/`decide_next_dispatch` run; short-circuit to the blocked shape on contention
- Add the new `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_session_id": ...}` shape to `sdlc-tool next-skill`/`dispatch record` output when the lock check fails
- Write unit tests simulating two sessions calling `ensure_session()`/`record_dispatch_for_session()`/`next-skill` for the same issue, including the continuing-session (non-cold-start) path

### 4. Wire lock renewal into worker heartbeat and local sdlc-tool mutations
- **Task ID**: build-renewal-surfaces
- **Depends On**: build-lock-primitive
- **Validates**: `tests/unit/test_session_executor_tick_backstop.py` (heartbeat renewal), `tests/unit/test_sdlc_dispatch.py` (stage-marker renewal)
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `touch_issue_lock` renewal call to `agent/session_executor.py::_heartbeat_loop`'s **tier-1 (60s) block** (not the 25-minute calendar block), guarded on `agent_session.session_type == "eng"` and a resolved `agent_session.issue_number`
- Add renewal as a side effect of `sdlc-tool stage-marker` and `dispatch record` subcommands only (single shared helper, reused by both) — `verdict record`/`meta-set` are deliberately excluded per the narrowed scope in Technical Approach
- Write unit tests confirming renewal extends the lock TTL on each of these call paths, and confirming `verdict record`/`meta-set` do not touch the lock

### 5. Update `/sdlc` skill interpretation of the new blocked reason
- **Task ID**: build-sdlc-skill-update
- **Depends On**: build-wire-checkpoints
- **Validates**: manual review of `.claude/skills/sdlc/SKILL.md` Step 4 language
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a line to `.claude/skills/sdlc/SKILL.md` Step 4 (or its `docs/sdlc/` addendum) noting the `ISSUE_LOCKED` blocked reason is surfaced identically to existing guard blocks: report to human, do not loop

### 6. Integration validation
- **Task ID**: validate-integration
- **Depends On**: build-wire-checkpoints, build-renewal-surfaces, build-status-filter, build-sdlc-skill-update
- **Assigned To**: lock-validator-docs
- **Agent Type**: validator
- **Parallel**: false
- Simulate the #1915 scenario: two `ensure_session()` calls for the same issue number in sequence, assert only the first proceeds to dispatch
- Simulate the continuing-session scenario: two `record_dispatch_for_session()` calls (no intervening `ensure_session()`) for the same issue number, assert only the first proceeds
- Confirm `sdlc-tool next-skill --issue-number N` returns the blocked shape for the second session
- Confirm existing G1-G7 guard behavior is unaffected when no contention exists
- Run full unit test suite for touched files

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: lock-validator-docs
- **Agent Type**: validator
- **Parallel**: false
- Create `docs/features/sdlc-issue-ownership-lock.md`
- Add entry to `docs/features/README.md`
- Update `docs/features/session-recovery-mechanisms.md` and `.claude/skills/sdlc/SKILL.md`/`docs/sdlc/sdlc.md` cross-references

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-lock-primitive, build-status-filter, build-wire-checkpoints, build-renewal-surfaces, build-sdlc-skill-update, validate-integration, document-feature
- **Assigned To**: lock-validator-docs
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all Success Criteria met including documentation
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lock primitive exists | `grep -c "def touch_issue_lock" models/session_lifecycle.py` | output > 0 |
| All call sites wired | `grep -rln "touch_issue_lock" tools/sdlc_session_ensure.py tools/sdlc_dispatch.py agent/session_executor.py \| wc -l` | output > 0 |
| find_session_by_issue filters terminal by default | `grep -c "include_terminal" tools/_sdlc_utils.py` | output > 0 |
| No terminal-session leak in default path | `grep -n "def find_session_by_issue" -A 5 tools/_sdlc_utils.py \| grep -c "include_terminal: bool = False"` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, History & Consistency | `next-skill`'s `decide()` resolves state via read-only `query_enriched()` and calls the unmodified, pure `decide_next_dispatch(stage_states, meta, context)` — there is no code path by which `next-skill` can emit the claimed `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` shape; the plan's own Technical Approach excludes `sdlc_next_skill.py` from the touch_issue_lock wiring list and explicitly excludes changes to `decide_next_dispatch()`'s guard table. | Revised — Solution/Technical Approach, Task 3 | Added a `touch_issue_lock(issue_number, session_id, peek=True)` pre-check at the top of `tools/sdlc_next_skill.py::decide()` that short-circuits to `{"blocked": True, "reason": "ISSUE_LOCKED", "owner_session_id": ...}` before `_resolve_enriched`/`decide_next_dispatch` run. |
| BLOCKER | Risk & Robustness, History & Consistency | `tools/_sdlc_utils.py::find_session(ensure=True)` — the resolver all four `sdlc-tool` mutation subcommands use — short-circuits at Step 2 (`find_session_by_issue()` match) and returns immediately for any existing session, terminal or not, **without ever calling `ensure_session()`**. Since a continuing pipeline session is the common case, both the "derive issue_number from the value ensure_session() receives" instruction and the "CLI mutation subcommands renew the lock as a side effect" claim have no working code path in practice — `ensure_session()` is effectively only reached on true cold-start. | Revised — Solution/Technical Approach, Task 3 | CLI subcommand handlers now call `touch_issue_lock()` directly, independent of whether `find_session()` short-circuited at Step 2 or reached `ensure_session()` at Step 4; `record_dispatch_for_session()` derives `issue_number` by parsing `session.issue_url` (mirroring `find_session_by_issue`'s `target_suffix` pattern) rather than assuming an `ensure_session()` call preceded it. |
| CONCERN | Risk & Robustness | `agent/session_executor.py::_heartbeat_loop` operates on two distinct objects (`session` gets `last_heartbeat_at` every tick; `agent_session` gets `updated_at`/calendar only every 25 min). The plan's guard `session.session_type == "eng"` doesn't specify which object carries `session_type`/`issue_number` in this scope, and placing renewal in the wrong cadence block (25-min) would blow past the 300s TTL. | Revised — Technical Approach, Task 4 | Confirmed by reading `agent/session_executor.py:1945-1983` that `agent_session.session_type`/`agent_session.issue_number` are in scope at the tier-1 (60s) tick point; renewal is placed there explicitly, guarded on `agent_session.session_type == "eng"` and a resolved `agent_session.issue_number`, not in the 25-minute calendar block. |
| CONCERN | Scope & Value | The new `issue_number` mirror field is explicitly "purely a read-side mirror" that never gates the decision — a schema change plus multi-site renewal wiring for a debugging convenience not required by any Acceptance Criterion. | Revised — Solution, Task 1 | `issue_number` is now written once at session creation (parsed from `issue_url`), never re-written on renewal call sites. |
| CONCERN | Scope & Value | Local-CLI renewal is wired into all four `sdlc-tool` mutation subcommands (`stage-marker`, `dispatch record`, `verdict record`, `meta-set`); `verdict record`/`meta-set` don't gate concurrent BUILD-stage-or-later progression, so renewing there is speculative rather than required by the stated problem. | Revised — Solution/Technical Approach, Task 4 | Renewal is now scoped to `stage-marker` and `dispatch record` only — the two subcommands that correspond to actual BUILD-or-later progression checkpoints; `verdict record`/`meta-set` are explicitly excluded with rationale in Technical Approach. |
| NIT | Scope & Value | Four named agents (lock-primitive-builder, call-site-builder, lock-validator, lock-docs) across 8 tasks is heavy process scaffolding for a Medium-appetite bug fix that is fundamentally one lock primitive plus a handful of call sites. | Revised — Team Orchestration | Collapsed to two agents (`core-builder`, `lock-validator-docs`); task assignments updated accordingly. |
| CONCERN | Structural check | Test Impact cites `tests/unit/test_session_executor.py` for heartbeat-renewal tests, but no such file exists — the module's tests are split across `test_session_executor_tick_backstop.py`, `test_session_executor_guards.py`, etc. | Revised — Test Impact, Task 4 | Pointed the heartbeat-renewal test case at the correct existing file, `tests/unit/test_session_executor_tick_backstop.py`. |
| BLOCKER (round 2) | Risk & Robustness | `ensure_session()`'s lock wiring (as drafted after round 1) only covered the cold-start create path — the function has 4 additional `return` statements (env-owns-issue, env-diverges-but-issue-owned, `find_session_by_issue` match, idempotent `existing_by_id` match) that exit before reaching it. This is exactly the scenario that caused the original #1915 duplicate-PR incident: a continuing session resolves via one of these early returns and never touches the lock. | Revised — Key Elements, Technical Approach, Task 3 | `touch_issue_lock` is now called from a single shared local helper invoked immediately before *each* of the five `return` statements in `ensure_session()`, confirmed by direct read of `tools/sdlc_session_ensure.py`. |
| BLOCKER (round 2) | Risk & Robustness | The lock's ownership credential was keyed on `session_id`, but `tools/sdlc_session_ensure.py::ensure_session()` derives the *same* deterministic `session_id` (`sdlc-local-{issue_number}`) for a given issue regardless of which process resolves it — confirmed by direct read (`local_session_id = f"sdlc-local-{issue_number}"`, no per-process/per-machine differentiator anywhere in the function). Two independently-live processes (local CLI vs. worker) racing the same issue would each pass the identical `session_id` string and each see the lock as "already held by me," never detecting contention — reproducing the exact failure mode this plan exists to close. | Revised — Key Elements, Technical Approach, Task 1 | Introduced a process-unique `holder_token` (`uuid.uuid4().hex`, cached per-process via `_process_holder_token()`) as the actual comparison key in `touch_issue_lock`'s stored Redis value (a small JSON payload also carrying `session_id`/`pid`/`hostname` for human-readable owner surfacing only). Ownership comparison and renewal now check `holder_token` equality, never `session_id` equality. |

---

## Open Questions Resolution

Three open questions were carried from initial drafting. Resolved before build using the plan's own stated defaults (all are tunable/reversible implementation details, not business decisions):

1. **`ISSUE_LOCK_TTL_SECONDS` default:** Keep 300s (5x the 60s heartbeat interval) as specified in Technical Approach. It's env-overridable, so if operational experience shows it's too tight (mirroring `claim_pending_run`'s 30s window being sized for a much shorter-lived claim), it can be tuned without a code change.
2. **Attach-mode on `ISSUE_LOCKED`:** v1 surfaces only the owner's `session_id` in the blocked response, per the plan's own suggested fallback. Read-only attach (`valor-session status --id <owner_session_id>`) is a natural follow-up but not required to close this issue's acceptance criteria.
3. **Pre-revival lock check in `crash_recovery.py`:** Not required as a separate gate. `touch_issue_lock()` is already checked at `ensure_session()` (the cold-start path a revived session goes through) and at `record_dispatch_for_session()` (before any stage dispatch) — per the Race 1 analysis, both checkpoints fire before any real BUILD-or-later work happens, so a revived terminal session that finds the issue already owned steps aside at the same checkpoint a genuinely new session would.
