---
status: Planning
type: bug
appetite: Medium
owner: Dev (Lane 1)
created: 2026-07-30
tracking: https://github.com/yudame/ai/issues/2446
last_comment_id:
---

# SDLC Run Self-Recognition (leases, owned run_id set, loud marker-write failure)

Closes #2446 and #2451 — one defect seen from two sides.

## Problem

A logical SDLC supervision run carries, over its lifetime, **more than one
`run_id`**, but nothing records that fact — so the run cannot tell *self* from
*other*. Concretely: the per-issue lock (`session:issuelock:{N}`) is renewed by
the standalone worker's in-process 60s heartbeat (`_tick_issue_lock_renewal`),
but the pure-local `/do-sdlc` supervisor has no equivalent renewer. When the
lease lapses (mid-stage, or across a stage boundary where a write failed), the
next `session-ensure` mints a **fresh** `run_id` and *overwrites*
`AgentSession.active_run_id`, discarding the prior identity. From that moment:

- **#2446 view (identity-loss / abort):** a stage fork still carrying the
  earlier `run_id` fails `_validated_reuse_candidate` (the live lock owner is
  now the new id, and `active_run_id` is also the new id), so its own
  hand-off reads as *foreign* — the fork pattern-matches `SUPERVISED_RUN_ACTIVE`
  / `ISSUE_LOCKED` and stands down. The #2421 fork received
  `SUPERVISED_RUN_ACTIVE` with `owner_session_id: "sdlc-local-2421"` — **its own
  anchor** — and abandoned the run.
- **#2451 view (duplicate build / retroactive back-write):** two run_ids
  co-exist for one run; the "loser" back-writes a stage marker under a run_id
  the ledger no longer recognizes (`LEASE_ABSENT`), or a second BUILD is
  dispatched because the first is not recognized as self.
- **#2451 folded-in (silent degraded ledger):** the #2439 run (PR #2450) had
  marker writes failing *repeatedly* across the whole run (`LEASE_ABSENT`, then
  `FAILED to write DOCS=completed`) while the agent passed the correct run_id
  every time. The writes were retried into place, so final ledger state was
  correct — but **nothing noticed the storm** because no stage reads the ledger
  along the way. A run can report success end-to-end with a ledger that was
  broadly unwritable throughout.

**Current behavior:** the run forgets its own prior identities; a lapsed-then-
re-minted lease turns a fork's legitimate hand-off into an abort or a duplicate,
and a sustained marker-write failure is invisible.

**Desired outcome:** a supervision run can answer "is this session mine?" from
**recorded state** (structural, not runtime-inferred). A lapsed lease is
*recoverable* rather than fatal; the right thing (inherit the live run) is the
default rather than a refusal; and a sustained marker-write failure is **loud**,
distinguishing transient-and-recovered from never-landed.

## Freshness Check

**Baseline commit:** `7368b1a8848153b7b1701fb817e3c092689f51c6`
**Issues filed at:** #2446 `2026-07-29T08:43:17Z`, #2451 `2026-07-29T09:34:28Z`
**Disposition:** Unchanged

**Recon note:** the recon-validation gate
(`validate_issue_recon.py`) reports no `## Recon Summary` header on either
issue — expected, since both were authored directly by the owner with binding
scope decisions in the comments, not via `/do-issue`. Independent recon was
performed for this plan (full substrate map with file:line grounding — see
Technical Approach); every claim below was re-verified against current `main`.

**File:line references re-verified against `main`:**
- `models/session_lifecycle.py:848` — `ISSUE_LOCK_TTL_SECONDS = int(os.environ.get("ISSUE_LOCK_TTL_SECONDS", "1800"))` — **default is 1800s, not 300s.** The "~300s" in #2446 traces to stale docstrings, not the live constant. Constant introduced at its current form in #1954 (`0f33567e9`, 2026-07-09).
- `tools/sdlc_session_ensure.py:44,180` and `agent/session_executor.py:178` — stale "300s TTL" docstrings. Still present; to be corrected.
- `tools/sdlc_session_ensure.py:307` — `session.active_run_id = candidate; session.save()` — single-value overwrite, confirmed. No `owned_run_ids` field exists.
- `tools/sdlc_session_ensure.py:108-152` — `_validated_reuse_candidate` proof logic, confirmed (live-owner-match OR free-lock+mirror-match).
- `tools/sdlc_session_ensure.py:225-254` — bare-ensure `SUPERVISED_RUN_ACTIVE` refusal, confirmed; no self-vs-foreign check before refusing.
- `agent/session_executor.py:165-238` — worker `_tick_issue_lock_renewal` (60s tier-1 tick); the local supervisor has no equivalent.
- `tools/sdlc_stage_marker.py::write_marker` — marker write path; `_DIAGNOSED_ERRORS` + "FAILED to write …" stderr, confirmed. No per-run success/failure tally.

**Commits on main since issues filed (touching substrate files):** none
(`git log --since=2026-07-29` over all six substrate files returned empty).

**Active plans in `docs/plans/` overlapping this area:** none matching
`2446|2451|lease|run-id|supervis`.

## Prior Art

- **#2026 (umbrella) / PR #2076**: "single-owner lease, in-turn stage work,
  verdict-gated routing, revision-latch fix." Introduced the supervised-run
  signal + `--reuse-run-id` claim-echo. Documented the exact lease-TTL churn
  symptom and resolved it with **manual** `--reuse-run-id` mitigation — a
  durable structural fix was never landed. This plan lands it.
- **#2144 / PR #2166**: "Self-heal SDLC run identity on resumed pipeline turns."
  `tools/_sdlc_run_identity.py` re-establishes a run_id after loss but does not
  give the run a *memory* of the identities it has held — so it cannot recognize
  a fork carrying a prior id as self.
- **#2187 (WS-F) / #2190**: adopt ownerless bridge PM session, avoid duplicate
  `sdlc-local` mint. Related dedup work; the env short-circuit in
  `ensure_session` is the adjacent code.
- **#2028 / #2042**: the `is_ledger` guard (non-executable ledger anchors). The
  owner's correction on #2446 confirms this guard is **correct and working** —
  explicitly out of scope; do not re-litigate.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2076 (#2026) | Single-owner lease + supervised-run signal + `--reuse-run-id` claim-echo | Claim-echo only proves continuity while the lock is *still live* (owner match) or `active_run_id` *still equals* the claim. A TTL lapse + re-mint breaks both: the lock is now owned by a new id and `active_run_id` was overwritten. The run has no record of its prior id, so a fork carrying it reads as foreign. |
| PR #2166 (#2144) | Self-heal run identity on resumed turns | Re-mints/re-establishes an id but keeps the single-value mirror. No accumulated identity set → still cannot recognize an older self-id. |
| — (manual) | Operator runs `--reuse-run-id` after the fact | Human-in-the-loop mitigation, not a durable mechanism; fails exactly when the supervisor is a headless per-turn subprocess with no operator. |

**Root cause pattern:** every prior fix treated the run's identity as a
**single current value** and tried to keep that one value consistent. The
durable fix is to record the **set** of identities the run has owned, so
self-recognition is answerable from recorded state regardless of how many times
the lease lapsed and re-minted.

## Architectural Impact

- **New dependencies**: none (no new libraries; one additive nullable Popoto field).
- **Interface changes**: `_validated_reuse_candidate` return semantics widen
  (may return the *live* owner id on self-recognition, not just the claim);
  `AgentSession` gains `owned_run_ids` (JSON list, nullable). New read-only
  subcommand `sdlc-tool run-health`. No change to the *shape or text* of the
  `SUPERVISED_RUN_ACTIVE` / `ISSUE_LOCKED` refusal payloads (Lane 3 / #2452 owns
  that prose).
- **Coupling**: decreases fragility — self-recognition removes the implicit
  coupling between "lease still live" and "run still valid."
- **Data ownership**: the run's identity history moves from an implicit
  single-value mirror to an explicit accumulated set on the anchor session.
- **Reversibility**: high. The field is additive/nullable; the self-recognition
  checks are additive proof branches (fall through to existing behavior when the
  set is empty/absent); observability counters are best-effort Redis keys with
  TTL. Nothing removes an existing guard.

## Appetite

**Size:** Medium

**Team:** Solo dev (builder), code reviewer

**Interactions:**
- PM check-ins: 1-2 (this is Lane 1; coordinate write-path files with Lane 2/#2447 and refusal-prose with Lane 3/#2452)
- Review rounds: 1-2 (substrate change — critique + PR review both gate)

## Prerequisites

No prerequisites — this work has no external dependencies. It runs against the
existing Redis/Popoto substrate already required by the SDLC pipeline.

## Solution

### Key Elements

- **`AgentSession.owned_run_ids`** — an additive, nullable JSON list of every
  `run_id` this logical supervision run has minted/bound, in order. The recorded
  state that makes self-recognition structural.
- **Self-recognition in `session-ensure`** — two additive proof branches:
  (a) `_validated_reuse_candidate` recognizes a claim in `owned_run_ids` as self
  even when the current lock owner is a *newer* id from the same run, and returns
  the live owner id so renewal succeeds; (b) the bare-ensure `SUPERVISED_RUN_ACTIVE`
  path recognizes the signal as *self* (signal `owner_session_id` == this
  session's id, or signal `run_id` ∈ `owned_run_ids`) and inherits/renews instead
  of refusing. "Make the right thing the default," not a new gate.
- **Local-supervisor lease heartbeat** — `tools/sdlc_lease_heartbeat.py`, a
  detached background renewer that gives the pure-local `/do-sdlc` supervisor the
  same protection the worker's `_tick_issue_lock_renewal` gives worker-run
  pipelines. Renews every `ISSUE_LOCK_TTL_SECONDS/3`; self-terminates when the
  lease is no longer owned by its run_id or after a max lifetime. Plus: correct
  the stale 300s→1800s docstrings and mark the TTL as tunable.
- **Loud marker-write observability** — `write_marker` records per-run
  success/failure tallies (Redis, TTL'd); a new read-only `sdlc-tool run-health`
  reports them and the "never-landed vs transient-and-recovered" disposition;
  `verdict selfcheck` asserts the run landed **at least one** successful marker
  write with a live lease (so an all-failing "degraded ledger" run cannot report
  success silently).

### Flow

Local `/do-sdlc` run starts → `session-ensure` mints `run_id` R1, records it in
`owned_run_ids=[R1]`, launches the detached lease-heartbeat → stage forks renew
under R1 → (lease lapses despite heartbeat, e.g. Redis hiccup) → next
`session-ensure` mints R2, appends → `owned_run_ids=[R1,R2]` → a fork still
carrying R1 calls `session-ensure`/marker-write → **self-recognized** (R1 ∈
owned set), renews under R2 instead of aborting → stage completes → at run end
`run-health` reports "0 never-landed writes" → `verdict selfcheck` confirms ≥1
live-lease write → merge proceeds.

### Technical Approach

**Part 2 — run-owned set of run_ids (load-bearing; build first).**

1. `models/agent_session.py`: add `owned_run_ids = Field(null=True)` (JSON-string
   encoded list), directly after `active_run_id` (~line 244). Additive nullable —
   **no data migration needed** (descriptor healing covers it, per #1099/#1172;
   see `feedback_field_backcompat_heal`). Add two small helpers on the model (or
   in `sdlc_session_ensure`): `_read_owned_run_ids(session) -> list[str]` and
   `_append_owned_run_id(session, run_id)` (dedup-preserving-order, cap length to
   a small bound to prevent unbounded growth on very long runs).
2. `tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind`: at the successful
   bind point (line 307), in addition to `active_run_id = candidate`, append
   `candidate` to `owned_run_ids` and save. The post-save readback (lines 324-359)
   continues to assert `active_run_id`; extend it to tolerate the list write.
3. `tools/sdlc_session_ensure.py::_validated_reuse_candidate`: add proof branch —
   if the live lock owner id != claim but **both** the claim and the owner id are
   in `owned_run_ids`, this is self across a re-mint: return the **live owner id**
   (so `touch_issue_lock` renews under the identity that actually holds the lock).
   If the lock is free and the claim ∈ `owned_run_ids` (not just == active_run_id),
   return the claim (re-acquire under a prior self identity). Existing branches
   unchanged; new branches only *widen* what counts as self.
4. `tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind` (bare-ensure
   supervised-run block, lines 225-254): before returning `SUPERVISED_RUN_ACTIVE`,
   check self: if `supervised.session_id == getattr(session, "session_id", None)`
   **or** `supervised.run_id in owned_run_ids(session)`, do NOT refuse — fall
   through to contest/renew the lock under `supervised.run_id` (treat as a
   verified reuse of that id) and return a normal success payload carrying it.
   The refusal payload is emitted **only** for a genuine foreign owner; its
   shape/text is untouched (Lane 3 constraint).

**Part 1 — local-supervisor lease renewal / heartbeat.**

5. New `tools/sdlc_lease_heartbeat.py` (CLI + `main()`): args `--issue-number`,
   `--run-id`, `--session-id`, optional `--interval` (default
   `ISSUE_LOCK_TTL_SECONDS//3`) and `--max-lifetime` (default e.g. 4h, tunable).
   Loop: `touch_issue_lock(issue, run_id, ...)`; if not `acquired` (lease lost to
   a foreign owner) or the owner id is no longer this run_id → exit 0 (run ended /
   superseded); sleep `interval`; repeat until max-lifetime. Best-effort, never
   raises. Mirrors `_tick_issue_lock_renewal`'s identity discipline (renew only,
   never mint — it refuses to `SET NX` a free key it does not already own).
6. Auto-launch: on a **fresh local mint** (not `--reuse-run-id`, and not under
   the worker — detect via the executor's env marker; if unresolvable, default to
   launch since duplicate renewers are idempotent), `_acquire_run_lock_and_bind`
   spawns the heartbeat as a **detached** subprocess (`start_new_session=True`,
   stdio to a log). This keeps the wiring in the tool layer, so **no `/do-sdlc`
   skill-body edit is required** — zero conflict surface with Lane 3. (Open
   Question 1 records the alternative of a one-line launch in the `/do-sdlc`
   run-setup section.)
7. Correct stale docstrings: `sdlc_session_ensure.py:44,180`,
   `session_executor.py:178` → 1800s. Add a grain-of-salt "provisional/tunable"
   comment at the `ISSUE_LOCK_TTL_SECONDS` definition
   (`feedback_provisional_magic_numbers`).

**Part 3 — loud marker-write observability.**

8. `tools/sdlc_stage_marker.py::write_marker`: on each write, increment a per-run
   Redis counter — `sdlc:marker_writes:{issue}:{run_id}:ok` on success,
   `:fail` on a non-degraded failure (state-machine rejection or `LEASE_ABSENT`;
   the deliberate Redis-absent "degraded quiet exit 0" is NOT counted as fail).
   Record the last-failed stage name. TTL the keys to the lock TTL × a small
   factor. Best-effort; a counter write failure never changes the marker outcome.
   (This is the write-path change Lane 2 / #2447 must rebase on.)
9. New read-only `sdlc-tool run-health --issue-number N --run-id X`
   (`tools/sdlc_run_health.py`): reports `{ok_writes, fail_writes, last_failed_stage,
   trail_complete, disposition}` where `disposition ∈ {clean, transient_recovered,
   never_landed}`. `trail_complete` is read from `stage-query` (read-only).
   `never_landed` = `fail_writes>0` AND the trail is missing a stage that a write
   was attempted for; `transient_recovered` = `fail_writes>0` AND trail complete.
   The `/do-sdlc` supervisor is expected to call this at run end and surface a
   non-clean disposition in its final report (documented, not enforced — no new
   gate).
10. `tools/sdlc_verdict.py::selfcheck` (the `verdict selfcheck` read used by the
    router to gate advance-past-REVIEW): add a check that this run recorded
    **≥1 `ok` marker write** (`sdlc:marker_writes:{issue}:{run_id}:ok > 0`). A run
    whose entire trail was reconstructed by retry with zero confirmed live-lease
    writes fails selfcheck loud — closing the "degraded ledger reports success"
    gap. This asserts *writability was proven at least once*, per the folded-in
    #2439 suggestion. Fail-closed only on the already-terminal REVIEW gate (not a
    new mid-pipeline gate) — it hardens an existing gate rather than adding one.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new self-recognition branches in `_validated_reuse_candidate` and
  `_acquire_run_lock_and_bind` must be exception-isolated (a malformed
  `owned_run_ids` JSON must fall through to existing behavior, never raise). Test:
  corrupt `owned_run_ids` → ensure returns a valid ISSUE_LOCKED/mint result.
- [ ] `sdlc_lease_heartbeat` loop wraps each iteration in try/except and exits
  cleanly on lost ownership. Test asserts it terminates (not spins) when the lock
  is foreign-owned.
- [ ] `write_marker` counter increments are best-effort: a Redis failure on the
  counter must not change the marker write's own exit code. Test: patch the
  counter write to raise → assert marker outcome unchanged.

### Empty/Invalid Input Handling
- [ ] `owned_run_ids` empty/None → all new branches fall through to existing
  behavior (identity-loss recovery is a no-op improvement, never a regression).
- [ ] `run-health` with an issue that has no counters → `disposition: clean`,
  zero counts (not an error).
- [ ] `sdlc_lease_heartbeat` with a run_id that owns nothing → exits 0 immediately
  (never mints).

### Error State Rendering
- [ ] `run-health` `never_landed` disposition must render loudly (stderr line +
  non-clean JSON field) so the supervisor's final report surfaces it.
- [ ] `verdict selfcheck` failure on zero-ok-writes must return `ok:false` with a
  named reason (`NO_CONFIRMED_MARKER_WRITE`), not a silent pass.

## Test Impact

- [ ] `tests/unit/` (sdlc session-ensure suite, e.g. `test_sdlc_session_ensure*.py`) — UPDATE: add cases for `owned_run_ids` accumulation, self-recognition across re-mint, and self-recognition in the supervised-run bare-ensure path. Existing reuse/ISSUE_LOCKED cases must still pass unchanged.
- [ ] `tests/` verdict/selfcheck suite (e.g. `test_sdlc_verdict*.py`) — UPDATE: add the zero-ok-writes `selfcheck` fail case; assert existing selfcheck passes are unaffected when ≥1 ok write exists.
- [ ] `tests/` stage-marker suite (e.g. `test_sdlc_stage_marker*.py`) — UPDATE: assert counter increments on ok/fail and that the degraded (Redis-absent) path is NOT counted as fail.
- [ ] New `tests/unit/test_sdlc_lease_heartbeat.py` — CREATE: terminate-on-foreign-owner, renew-on-self-owner, exit-immediately-on-no-ownership.
- [ ] New `tests/unit/test_sdlc_run_health.py` — CREATE: the three dispositions.

Existing behavior tests (reuse claim-echo, ISSUE_LOCKED foreign holder,
no-adopt invariant) are load-bearing regressions — they MUST remain green; the
new branches only widen self-recognition, never narrow foreign refusal.

## Rabbit Holes

- **Rewriting the lease/lock model.** Do not. The lock (`touch_issue_lock` /
  `release_issue_lock`) and the `is_ledger` guard are correct (owner-confirmed).
  This plan only *records identity history* and *reads it back*; it does not
  change lock acquisition semantics.
- **A persistent in-process heartbeat thread inside the per-turn `claude -p`
  supervisor.** An LLM turn cannot reliably host a background thread across its
  lifetime. The detached subprocess heartbeat is the right shape; do not try to
  thread it into the agent turn.
- **New refusal gates / approval steps / polled locks.** Explicitly forbidden by
  the owner (see No-Gos). Self-recognition + right-thing-default only.
- **Changing `SUPERVISED_RUN_ACTIVE` / `ISSUE_LOCKED` payload shape or text.**
  Lane 3 / #2452 owns that prose. If a change here would alter a refusal
  payload's shape or text, STOP and report to the PM.

## Risks

### Risk 1: Self-recognition widened too far → a genuine foreign run mistaken for self
**Impact:** two independent pipelines on one issue could collide (the #1915 duplicate-PR class).
**Mitigation:** the owned-set is written ONLY by this session's own `_acquire_run_lock_and_bind` on a bind it won — a foreign process never writes into another session's `owned_run_ids`. Self-recognition additionally requires the id to be in *this* session's recorded set (or the signal's `owner_session_id` to equal *this* session's id). The no-adopt invariant is preserved: we never read a run_id OUT of the lock/foreign record to impersonate; we only match a claim the caller already carried against *our own* recorded history. Regression tests assert a foreign holder still yields ISSUE_LOCKED.

### Risk 2: Detached heartbeat leaks (zombie renewers keeping a dead run's lease alive)
**Impact:** a crashed supervisor's heartbeat could hold the lease past the run, blocking a legitimate successor.
**Mitigation:** the heartbeat self-terminates the moment the lease is no longer owned by its run_id, and after a bounded max-lifetime regardless. It only *renews* (never `SET NX` on a free key), so once the lock lapses and a successor acquires it, the old heartbeat's next tick sees a foreign owner and exits. Test asserts termination.

### Risk 3: Coordination — Lane 2 (#2447) rebases on my write-path changes
**Impact:** merge conflict / duplicated logic in `sdlc_stage_marker.py`, `sdlc_verdict.py`, `_sdlc_utils.py`.
**Mitigation:** land Part 3's write-path changes early and note exact files/functions in the PR body for Lane 2. Keep the counter logic to a small, clearly-delimited helper so Lane 2's verdict/marker changes rebase cleanly.

## Race Conditions

### Race 1: Concurrent append to `owned_run_ids`
**Location:** `tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind` (bind point).
**Trigger:** two `session-ensure` calls for the same session racing to append.
**Data prerequisite:** the append must read-modify-write the list on the session record.
**State prerequisite:** the lock contest (`SET NX EX`) already serializes *who wins the run_id*; only the winner appends. The loser is refused before the append.
**Mitigation:** the append happens only on the branch that already won the lock (post-`SET NX` success), so it is effectively single-writer per bind. The append is dedup-preserving so a re-entrant same-id write is idempotent. Read-back after save (existing pattern) covers a lost write by falling through to existing behavior.

### Race 2: Heartbeat vs. successor acquisition
**Location:** `tools/sdlc_lease_heartbeat.py` renew loop vs. a successor `session-ensure`.
**Trigger:** lease lapses; successor acquires; old heartbeat ticks.
**Data prerequisite:** the lock payload's `run_id` field is the arbiter.
**State prerequisite:** `touch_issue_lock` renews only for the same owner id; a foreign-owned lock returns not-acquired.
**Mitigation:** the heartbeat's renew is a same-owner operation; against a successor-owned lock it returns not-acquired and the heartbeat exits. No `SET NX` on a free key from the heartbeat, so it cannot steal a lapsed lease back from a successor.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2452] Changing the `/do-sdlc` skill-body prose that documents
  the three `session-ensure` refusal payloads (`SUPERVISED_RUN_ACTIVE`,
  `ISSUE_LOCKED` orphaned true/false). Lane 3 owns it. This plan changes *when*
  `SUPERVISED_RUN_ACTIVE` is emitted (self-recognition), never its shape/text.
- [SEPARATE-SLUG #2447] Recording a REVIEW verdict without findings / the verdict
  persistence semantics beyond the single `≥1-ok-write` selfcheck assertion here.
  Lane 2 owns the broader verdict-write hardening; coordinate on shared files.
- Re-litigating the `is_ledger` guard (#2028/#2042) — owner-confirmed correct.
- Adding any new refusal gate, approval step, or polled lock — explicitly
  forbidden by the owner's design constraint on #2451.

## Update System

No update-system changes required — this feature is internal to the SDLC
substrate. The new `sdlc-tool` subcommands (`run-health`) and the
`sdlc_lease_heartbeat` module ship with the repo and are reachable via the
existing `sdlc-tool` console entry point. No new config files, secrets, or
cross-machine propagation. (Confirm `run-health` is wired into the `sdlc-tool`
dispatcher the same way existing subcommands are — see Agent Integration.)

## Agent Integration

- The new `run-health` subcommand must be registered in the `sdlc-tool`
  dispatcher (the same place `stage-query`, `verdict`, `stage-marker` are wired).
  `sdlc_lease_heartbeat` is invoked as `python -m tools.sdlc_lease_heartbeat`
  (detached child of `session-ensure`); it needs no MCP/bridge surface.
- No bridge (`bridge/telegram_bridge.py`) import changes — this is a
  supervisor/tool-layer change, not a conversational surface.
- Integration test: a scripted local-supervision flow that lapses and re-mints a
  lease, then asserts a fork carrying the old run_id is self-recognized and the
  pipeline continues (end-to-end proof of the #2446/#2421 fix).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sdlc-run-self-recognition.md` describing owned_run_ids,
  the self-recognition branches, the local-supervisor heartbeat, and run-health.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-issue-ownership-lock.md` and
  `docs/features/sdlc-local-supervision.md` to reference the heartbeat + owned-set.
- [ ] Update `docs/features/sdlc-verdict-fail-closed-persistence.md` to note the
  new `≥1-ok-write` selfcheck assertion.

### Inline Documentation
- [ ] Docstrings on `owned_run_ids`, the new proof branches, the heartbeat module,
  and `run-health`.
- [ ] Correct the stale 300s docstrings to 1800s.

## Success Criteria

- [ ] `AgentSession.owned_run_ids` accumulates every minted/bound run_id for a run.
- [ ] A stage fork carrying a prior self run_id is recognized as self (not
  refused) after a lease lapse + re-mint — proven by an integration test.
- [ ] The bare-ensure `SUPERVISED_RUN_ACTIVE` path recognizes its own anchor
  (`owner_session_id == self` or signal run_id ∈ owned set) and inherits instead
  of aborting (the #2421 case).
- [ ] A genuine foreign holder still yields `ISSUE_LOCKED` (regression green).
- [ ] The local `/do-sdlc` supervisor keeps its lease alive across a long stage
  via the detached heartbeat; the heartbeat self-terminates on lost ownership.
- [ ] Stale 300s docstrings corrected to 1800s; TTL marked tunable.
- [ ] A sustained marker-write failure surfaces via `run-health`
  (`never_landed`) and `verdict selfcheck` fails loud on zero confirmed writes.
- [ ] The refusal payload shapes/text of `SUPERVISED_RUN_ACTIVE` / `ISSUE_LOCKED`
  are byte-for-byte unchanged (anti-criterion; Lane 3 boundary).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (substrate)**
  - Name: `substrate-builder`
  - Role: implement owned_run_ids + self-recognition + heartbeat + observability (all parts; sequenced Part 2 → Part 1 → Part 3)
  - Agent Type: builder
  - Domain: async/concurrency + Redis/Popoto data
  - Resume: true

- **Validator (substrate)**
  - Name: `substrate-validator`
  - Role: verify self-recognition, foreign-refusal regression, heartbeat termination, observability dispositions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. owned_run_ids field + accumulation + self-recognition (Part 2)
- **Task ID**: build-self-recognition
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_session_ensure*.py (update/create)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Add `owned_run_ids` to `AgentSession`; accumulate on bind; widen `_validated_reuse_candidate`; add self-check before `SUPERVISED_RUN_ACTIVE`.
- Preserve no-adopt invariant + foreign-refusal regression tests.

### 2. local-supervisor lease heartbeat + docstring fix (Part 1)
- **Task ID**: build-heartbeat
- **Depends On**: build-self-recognition
- **Validates**: tests/unit/test_sdlc_lease_heartbeat.py (create)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- Create `tools/sdlc_lease_heartbeat.py`; auto-launch (detached) from fresh local mint; correct 300s docstrings; mark TTL tunable.

### 3. loud marker-write observability (Part 3)
- **Task ID**: build-observability
- **Depends On**: build-self-recognition
- **Validates**: tests for stage-marker counters, run-health, verdict selfcheck
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Counters in `write_marker`; new `run-health`; `verdict selfcheck` ≥1-ok-write assertion. **Note exact write-path files/functions for Lane 2 (#2447).**

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-heartbeat, build-observability
- **Assigned To**: substrate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run narrow-scope tests; verify all success criteria incl. refusal-payload anti-criterion; report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: substrate-builder (documentarian pass)
- **Agent Type**: documentarian
- **Parallel**: false
- Create/update docs listed in Documentation section.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| owned_run_ids field present | `grep -n "owned_run_ids" models/agent_session.py` | output contains owned_run_ids |
| self-recognition branch present | `grep -n "owned_run_ids" tools/sdlc_session_ensure.py` | output contains owned_run_ids |
| heartbeat module exists | `test -f tools/sdlc_lease_heartbeat.py && echo ok` | output contains ok |
| run-health subcommand exists | `test -f tools/sdlc_run_health.py && echo ok` | output contains ok |
| stale 300s docstrings gone | `grep -rn "300s TTL\|300s lock" tools/sdlc_session_ensure.py agent/session_executor.py` | exit code 1 |
| refusal payload text unchanged (anti-criterion) | `git diff main -- tools/sdlc_session_ensure.py agent/supervised_run.py \| grep -E '^\+' \| grep -E '"reason": "(SUPERVISED_RUN_ACTIVE\|ISSUE_LOCKED)"'` | exit code 1 |
| Lint clean | `python -m ruff check tools/ models/ agent/` | exit code 0 |
| Format clean | `python -m ruff format --check tools/ models/ agent/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Heartbeat wiring location.** Auto-launch the detached heartbeat from
   `session-ensure` on fresh local mint (tool layer, zero `/do-sdlc` edit → zero
   Lane 3 conflict) — the plan's default — vs. a one-line launch in the
   `/do-sdlc` run-setup section (more visible, but touches the file Lane 3 edits).
   Plan assumes the tool-layer default; confirm acceptable.
2. **`owned_run_ids` cap.** What upper bound on the retained set (a very long or
   pathological run could accumulate many re-mints)? Plan assumes a small cap
   (e.g. last 32, keeping the most recent) is sufficient for self-recognition;
   confirm no need for the full history.
3. **`run-health` at run end: documented vs. enforced.** Plan documents that the
   supervisor calls `run-health` and surfaces a non-clean disposition (no new
   gate, per owner). The only *enforced* half is the `verdict selfcheck`
   ≥1-ok-write assertion on the existing REVIEW gate. Confirm that hardening the
   existing REVIEW gate (not a new gate) is within the "refusal only where
   irreversible" allowance — merging on a never-written ledger is the irreversible
   case it guards.
