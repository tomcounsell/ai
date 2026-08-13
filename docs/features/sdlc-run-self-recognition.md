# SDLC Run Self-Recognition

**Status:** Shipped · **Issues:** [#2446](https://github.com/tomcounsell/ai/issues/2446), [#2451](https://github.com/tomcounsell/ai/issues/2451)

## Problem

A logical SDLC supervision run carries, over its lifetime, more than one
`run_id` (see [SDLC Issue Ownership Lock](sdlc-issue-ownership-lock.md) for
the run-identity model), but nothing recorded that fact. When the per-issue
lease lapsed and a fresh `session-ensure` minted a new `run_id`, the run
forgot its own prior identity:

- **#2446 (identity-loss / abort):** a stage fork still carrying the earlier
  `run_id` failed `_validated_reuse_candidate` (the live lock owner was now
  the new id), so its own hand-off read as *foreign* -- the fork
  pattern-matched `SUPERVISED_RUN_ACTIVE` carrying **its own anchor's**
  `run_id` and abandoned the run.
- **#2451 (duplicate build / retroactive back-write / silent degraded
  ledger):** two run_ids co-existed for one run, and a run's stage-marker
  writes could fail repeatedly (`LEASE_ABSENT`, state-machine rejection)
  while nothing noticed the storm -- the run reported success end-to-end with
  a ledger that was broadly unwritable throughout.

## Solution

Four additive mechanisms close both gaps without changing lock acquisition
semantics, the `is_ledger` guard, or the shape/text of the
`SUPERVISED_RUN_ACTIVE` / `ISSUE_LOCKED` refusal payloads.

### `AgentSession.owned_run_ids`

`models/agent_session.py` adds `owned_run_ids = Field(null=True)` directly
after `active_run_id` -- an additive, nullable JSON-string-encoded list of
every `run_id` this logical supervision run has minted/bound, in order. This
is the recorded state that makes self-recognition structural instead of
runtime-inferred.

- `tools/sdlc_session_ensure.py::_read_owned_run_ids(session)` parses the
  field tolerantly: malformed JSON, a non-list value, `None`, or an absent
  attribute all resolve to `[]` -- self-recognition degrades to existing
  behavior, never raises.
- `tools/sdlc_session_ensure.py::_append_owned_run_id(session, run_id)`
  appends dedup-preserving-order at the bind point in
  `_acquire_run_lock_and_bind` (alongside the existing
  `session.active_run_id = candidate` write, batched into the same
  `session.save()`). Capped to the most recent `OWNED_RUN_IDS_CAP` entries
  (default 32, env-overridable via `OWNED_RUN_IDS_CAP`) to bound growth on a
  very long or pathological run.

The set is written **only** by this session's own winning binds -- a foreign
process never writes into another session's `owned_run_ids`. It is
self-written history, never populated from a foreign lock or record.

### Two self-recognition branches

Both branches only *widen* what counts as self; the no-adopt invariant (a
live foreign holder always yields `ISSUE_LOCKED`) is unchanged.

**1. `_validated_reuse_candidate` -- self across a re-mint.** In addition to
the existing live-owner-match and free-lock-plus-mirror-match proofs, a new
branch recognizes: the live lock owner is a *different* id than the caller's
claim, but **both** the claim and the current owner id are in this session's
own `owned_run_ids` history. That means the lease lapsed and a later
self-ensure re-minted the owner id while a fork still carries an earlier one.
The function returns the **live owner id** (not the stale claim), so the
`touch_issue_lock` renewal that follows runs under the identity that
actually holds the lock. A companion branch on a *free* lock honors the
claim when it is anywhere in `owned_run_ids`, not just equal to the single
`active_run_id` mirror -- re-acquiring under a prior self identity after a
lease lapse.

**2. Bare-ensure `SUPERVISED_RUN_ACTIVE` self-check (the #2421 case).**
Before `_acquire_run_lock_and_bind`'s bare-ensure path (no `--reuse-run-id`)
returns the `SUPERVISED_RUN_ACTIVE` refusal for a live supervised-run signal,
it now checks: is that signal's `run_id` in **this session's own**
`owned_run_ids`? If so, the signal is self, not foreign -- the call falls
through to the normal contest under the supervisor's `run_id` (verified
reuse renews/re-acquires it) and returns a **normal success payload**
carrying it, never the refusal. A stage fork that reads its own anchor's
run_id back from the supervised-run signal now inherits it instead of
standing down.

**Why `session_id` equality is deliberately excluded (BLOCKER 2, incident
#1915).** The self-check is `supervised.run_id in owned_run_ids` **only** --
it is never `or`'d with `supervised.session_id == session.session_id`.
`touch_issue_lock`'s ownership contract decides ownership solely by
`run_id`, never by `session_id`
(`models/session_lifecycle.py:988-993`), precisely because two independent
processes can resolve the identical deterministic `sdlc-local-{N}` session_id
for the same issue -- that collision is what caused incident #1915's
duplicate-pipeline class in the first place. A `session_id`-equality arm
would wave a genuine foreign run through the self-check and reopen exactly
that class. The `owned_run_ids` membership test is safe because the set is
self-written history: it is populated only by this session's own winning
binds, never from a foreign signal or lock record, so a foreign run's
`run_id` can never appear in it.

### Local-supervisor lease heartbeat

The pure-local `/do-sdlc` supervisor -- a per-turn `claude -p` subprocess
blocked inside a synchronous stage call -- has no equivalent to the
standalone worker's in-process 60s tick
(`agent/session_executor.py::_tick_issue_lock_renewal`), so its lease could
lapse mid-stage. `tools/sdlc_lease_heartbeat.py` is the missing renewer:

- **Peek-first, renew-only (the load-bearing safety property).** Every tick
  peeks first (`touch_issue_lock(issue, run_id, peek=True)` -- a peek never
  mutates, whatever run_id it carries) and:
  - `peek.owner_run_id is None` (lease absent/lapsed) -> exit 0 immediately.
  - `peek.owner_run_id != run_id` (a successor owns it) -> exit 0 immediately.
  - `peek.owner_run_id == run_id` -> only then calls
    `touch_issue_lock(issue, run_id, ..., renew_only=True)` to extend the
    TTL. `renew_only=True` (issue #2714) makes the "never mint" guarantee
    structural rather than conventional: it skips both of
    `touch_issue_lock`'s minting branches and writes through a
    compare-and-set Lua script, so a release landing between the peek and
    the extend is a no-op rather than something the extend could undo.

  The exit conditions are deliberately **ownership checks, never pid-liveness
  inference** (issue #2537 review): the lease payload's `pid` is stamped by
  the short-lived `sdlc-tool session-ensure` CLI at acquire time and is dead
  before the detached heartbeat's first tick, so the peek's pid-keyed
  `orphaned_lock` signal reads every locally-minted lease as orphaned and
  must not gate the renew -- gating on it would lapse the lease mid-stage,
  the exact failure this heartbeat exists to prevent. Issue #2620 then fixed
  the signal itself at its source: each renewal re-stamps `renewed_at` on
  the payload, and `_lock_owner_is_live` treats a fresh stamp as proof of
  life, so *this loop's own ticking* is what keeps the lease reading live to
  every other consumer -- `orphaned_lock` freshness is therefore
  manufactured by the heartbeat itself, not an independent crash signal. The
  exit conditions here stay pure ownership checks regardless -- see
  [SDLC Issue Ownership Lock](sdlc-issue-ownership-lock.md).
  This mirrors `touch_issue_lock`'s own "no run_id supplied: never mutates"
  special case at the caller layer: the heartbeat can only ever *extend* a
  lease it already owns, never mint on a free key nor steal one from a
  successor.
- **Run liveness is the supervisor's liveness (issue #2714).** The
  heartbeat's own existence proves nothing about whether a supervisor is
  still driving the run, so `session-ensure` resolves the supervising
  `claude` process's `(pid, create_time)` at mint time and hands it to the
  heartbeat, which polls it every `SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS`
  (default 60s). Two consecutive positive-death observations release the
  lease and the supervised-run signal and exit -- a killed supervisor's
  lease is gone in roughly two minutes. When a supervisor identity resolves,
  the unchanged 4h `MAX_LIFETIME_SECONDS` remains a backstop, not the
  primary detector. When no identity resolves, a tighter 90-minute
  `UNSUPERVISED_MAX_LIFETIME_SECONDS` ceiling applies and that exit stops
  renewing **without** releasing (an unresolvable supervisor is not proof of
  death), leaving the lease to lapse on its own TTL.
- Renews every `ISSUE_LOCK_TTL_SECONDS // 3` by default (so ~3 renews per TTL
  window), overridable via `--interval`, decoupled from the supervisor-check
  cadence above.
- Best-effort throughout: every tick is wrapped in try/except; a Redis hiccup
  is swallowed and retried next tick.

**Self-recognition is now a narrower fallback.** Before issue #2714 the
heartbeat had no way to detect a dead supervisor directly, so a lapsed lease
under a live pipeline was the case `owned_run_ids` self-recognition existed
to recover from. The heartbeat now has an external liveness input -- the
polled supervisor identity -- so most crash scenarios are caught and released
by the heartbeat itself well before the lease lapses. Self-recognition
remains the recovery path for what the supervisor watch cannot see: a Redis
hiccup that drops the lease despite a live supervisor and a healthy
heartbeat, or any lapse on the unsupervised path where the ceiling
deliberately declines to release.

**Why a detached subprocess, not an in-turn thread.** An LLM turn (a `claude
-p` subprocess) cannot reliably host a background thread across its own
lifetime -- the turn itself blocks synchronously inside a stage call, and
there is no guaranteed opportunity for a thread to keep running independent
of that call stack. `tools/sdlc_session_ensure.py::_maybe_launch_lease_heartbeat`
therefore spawns the heartbeat as a **detached** subprocess
(`start_new_session=True`, stdio redirected to `logs/sdlc_lease_heartbeat.log`)
on a fresh **local** mint only -- skipped under the worker
(`VALOR_WORKER_MODE`, whose own tier-1 tick already renews) and under pytest.
Wiring stays entirely in the tool layer, so no `/do-sdlc` skill-body edit is
required.

The stale "~300s TTL" docstrings in `tools/sdlc_session_ensure.py` and
`agent/session_executor.py` are corrected to the actual default: 1800s
(`ISSUE_LOCK_TTL_SECONDS`, marked provisional/tunable at its definition in
`models/session_lifecycle.py`).

### Loud marker-write observability: run-health + the `≥1-ok-write` selfcheck gate

`tools/_sdlc_marker_telemetry.py` gives `tools/sdlc_stage_marker.py::write_marker`
a per-run Redis counter, incremented on every write outcome:

- `sdlc:marker_writes:{issue}:{run_id}:ok` (`INCR`) on a successful write.
- `sdlc:marker_writes:{issue}:{run_id}:fail` (`INCR`) on a non-degraded
  failure (state-machine rejection or `LEASE_ABSENT`). The deliberate
  Redis-absent "degraded quiet exit 0" path is **not** counted as a failure.
- `sdlc:marker_writes:{issue}:{run_id}:last_failed_stage` records the stage
  name of the most recent failure.

TTL is **refreshed on every increment** (`INCR` then `EXPIRE`, never a
one-shot `SET ... EX`): a full pipeline spans hours-to-days with review
pauses, so a set-once TTL scaled to the lock TTL would expire the first
ok-write's counter before REVIEW and false-fail the selfcheck gate below on a
healthy run. The window itself is generous (24h default, env-overridable via
`SDLC_MARKER_COUNTER_TTL_SECONDS`) so a legitimately paused run at REVIEW
still reads `≥1` ok-write. Every operation is best-effort: a counter-write
failure never changes the marker write's own outcome, and reads fail soft to
zero counts.

`tools/sdlc_run_health.py` (`sdlc-tool run-health --issue-number N --run-id
X`, wired into `scripts/sdlc-tool`'s `ALLOWED_SUBCOMMANDS`) is a read-only
report over those counters plus `stage-query`, computing one of three
dispositions:

| Disposition | Condition |
|---|---|
| `clean` | Zero fail writes (or no counters at all). |
| `transient_recovered` | Fail writes occurred, but the trail is complete for the stage a write targeted -- retries landed it. |
| `never_landed` | Fail writes occurred **and** the trail is missing a stage a write was attempted for -- a genuine hole. Rendered loudly on stderr so a supervising `/do-sdlc` run surfaces it in its final report (documented, not enforced -- no new gate). |

The one **enforced** half is `tools/sdlc_review_finalize.py::check_review_persistence`
(the function that actually backs both `verdict finalize` and `verdict
selfcheck` -- `tools/sdlc_verdict.py` only wires the subparser to it).
Immediately before setting `result["ok"] = True` on the APPROVED path, it now
asserts this run recorded **at least one** confirmed `ok` marker write
(`marker_ok_write_count(issue_number, effective_run_id) > 0`, resolving
`effective_run_id` from the explicit `--run-id` on the write path, or from
the current lease owner via a peek on the read-only `selfcheck` path). On
zero, `result["reason"] = "NO_CONFIRMED_MARKER_WRITE"` and the check returns
early -- the same `result["reason"] = ...; return result` pattern as the
existing `REVIEW_MARKER_INCOMPLETE` branch. A run whose entire trail was
reconstructed by retry with zero confirmed live-lease writes now fails
selfcheck loud instead of silently reporting success on an unwritable
ledger. This hardens the already-terminal REVIEW gate (see [SDLC Verdict
Fail-Closed Persistence](sdlc-verdict-fail-closed-persistence.md)) rather
than adding a new mid-pipeline gate.

## Flow

Local `/do-sdlc` run starts -> `session-ensure` mints `run_id` R1, records it
in `owned_run_ids=[R1]`, launches the detached lease-heartbeat -> stage forks
renew under R1 -> lease lapses despite the heartbeat (e.g. a Redis hiccup) ->
next `session-ensure` mints R2, appends -> `owned_run_ids=[R1,R2]` -> a fork
still carrying R1 calls `session-ensure`/marker-write and is **self-recognized**
(R1 is in the owned set), renewing under R2 instead of aborting -> stage
completes -> at run end, `run-health` reports "0 never-landed writes" ->
`verdict selfcheck` confirms `≥1` live-lease write -> merge proceeds.

## Source Files

| File | Role |
|---|---|
| `models/agent_session.py` | `owned_run_ids` field |
| `tools/sdlc_session_ensure.py` | `_read_owned_run_ids`, `_append_owned_run_id`, `_validated_reuse_candidate` (widened), `_acquire_run_lock_and_bind` (accumulation + supervised-self check + heartbeat launch), `_maybe_launch_lease_heartbeat` |
| `tools/sdlc_lease_heartbeat.py` | Peek-first, `renew_only=True`-extending detached lease-heartbeat CLI/loop (`run_heartbeat`); polls the supervisor identity and releases on confirmed death (issue #2714) |
| `tools/sdlc_supervisor_identity.py` | `resolve_supervisor_identity_detailed()` -- the supervisor `(pid, create_time)` resolver handed to the heartbeat at mint time (issue #2714) |
| `tools/_sdlc_marker_telemetry.py` | Per-run ok/fail marker-write counters (`record_marker_write`, `read_marker_counters`, `marker_ok_write_count`) |
| `tools/sdlc_stage_marker.py` | `write_marker` -- calls `record_marker_write` on every write outcome |
| `tools/sdlc_run_health.py` | Read-only `run-health` disposition report (`clean` / `transient_recovered` / `never_landed`) |
| `tools/sdlc_review_finalize.py` | `check_review_persistence` -- the `≥1-ok-write` `NO_CONFIRMED_MARKER_WRITE` selfcheck gate |
| `scripts/sdlc-tool` | `run-health` subcommand registration |

## Related

- [SDLC Issue Ownership Lock](sdlc-issue-ownership-lock.md) -- the `run_id`
  model, `touch_issue_lock`, and the no-adopt invariant this feature extends.
- [SDLC Local Supervision](sdlc-local-supervision.md) -- the `/do-sdlc`
  supervisor this heartbeat protects.
- [SDLC Run Identity Self-Heal](sdlc-run-identity-self-heal.md) -- the prior
  fix (#2144) that re-establishes a run_id after loss but kept a single-value
  mirror; this feature gives the run a memory of every identity it has held.
- [SDLC Verdict Fail-Closed Persistence](sdlc-verdict-fail-closed-persistence.md)
  -- the REVIEW gate this feature's selfcheck assertion hardens.
