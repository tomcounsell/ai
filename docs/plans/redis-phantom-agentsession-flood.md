---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2207
last_comment_id: 5053674965
revision_applied: true
revision_applied_at: 2026-07-23T03:00:18Z
---

# Harden AgentSession index rebuild against phantom re-inflation

## Problem

On 2026-07-22, Redis db0 held ~6.27M keys (grew to 7.39M) — almost all
`AgentSession:None:{uuid}:None:None:None:None` phantom hashes containing only
Popoto `$IndexF` index bookkeeping, no real session data. Normal keyspace is
thousands. The acute flood was purged (db0 → 18,986 keys, confirmed clean and
stable 2026-07-23 02:32Z) and the one-off remediation shipped. This plan closes
the two **durable** engineering gaps that let the flood happen and, worse,
amplified it.

**Current behavior:**
- `AgentSession.rebuild_indexes()` runs `field.on_save` for every field over
  every `AgentSession:*` hash (popoto `base.py:2849-2856`). Decoding an
  identity-less phantom hash regenerates its `AutoKeyField` uuid, so
  `INDEX_SWAP_LUA` HSETs onto a brand-new key — creating a fresh phantom. Every
  rebuild pass over a polluted keyspace **duplicates** the phantom population.
- The #2101/PR #2102 A1 guard (`AgentSession.repair_indexes`,
  `models/agent_session.py:2168-2203`) shims **only** the `status` field's
  `on_save` to skip identity-less hashes. The three other `IndexedField`s —
  `task_type`, `claude_session_uuid`, `claude_pid` — are un-shimmed. `MONITOR`
  caught `INDEX_SWAP_LUA` HSETting exactly those three fields + their
  `\x00idxset` pointers, matching the un-shimmed set exactly.
- Worker startup Step 1 (`worker/__main__.py:736-753`) calls
  `scripts.popoto_index_cleanup.run_cleanup`, which invokes **raw
  `model_class.rebuild_indexes()`** (`scripts/popoto_index_cleanup.py:216-220`),
  NOT the A1-guarded `repair_indexes()`. So Step 1 re-inflates **all four**
  fields, including `status`, with no guard at all.
- The 30s per-model timeout in `run_cleanup` is illusory:
  `with concurrent.futures.ThreadPoolExecutor(...) as executor:` —
  `future.result(timeout=30)` raises `TimeoutError`, but exiting the `with`
  block calls `executor.shutdown(wait=True)`, which blocks forever on the
  un-killable rebuild thread. This is the 8h zero-heartbeat wedge (worker PID
  36598); the wedged rebuild also re-inflated phantoms the entire time.

**Desired outcome:**
- `rebuild_indexes` over an identity-less-polluted keyspace re-inflates **zero**
  phantoms — for all four IndexedFields, on both the `repair_indexes` and the
  worker Step 1 code paths.
- Worker startup Step 1 can never block the serve loop: its per-model budget is
  actually enforced (a slow/poisoned model is abandoned, not waited on), so the
  worker heartbeats within threshold even against a degenerate keyspace.

## Freshness Check

**Baseline commit:** `9441f597b`
**Issue filed at:** 2026-07-22T07:45:20Z
**Disposition:** Unchanged

**File:line references re-verified against current main:**
- `models/agent_session.py:145,184,230,262` — the four `IndexedField`s
  (`status`, `task_type`, `claude_session_uuid`, `claude_pid`) — still hold.
- `models/agent_session.py:2168-2203` — A1 shim guards only `status.on_save` —
  still holds.
- `worker/__main__.py:736-753` — Step 1 calls `run_cleanup` — still holds.
- `scripts/popoto_index_cleanup.py:213-227` — `with ThreadPoolExecutor` +
  `future.result(timeout=30)` + implicit `shutdown(wait=True)` — still holds.
- popoto `base.py:2849-2856` — per-field `on_save` loop in `rebuild_indexes` —
  still holds (popoto 1.8.0, pip-installed, not vendored).

**Cited sibling issues/PRs re-checked:**
- #2101 / PR #2102 — CLOSED/MERGED; shipped the `status`-only A1 guard this plan
  generalizes.
- #2204 — catchup re-handling; a plausible historical *producer* of the churn,
  but the re-inflation *mechanism* is settled and independent. Out of scope.
- #1459, #2086 — prior class-set-orphan work; unchanged, complementary.

**Commits on main since issue was filed (touching referenced files):** none
(`git log --since=2026-07-22T07:45:20Z -- worker/__main__.py
scripts/popoto_index_cleanup.py models/agent_session.py` → empty).

**Active plans in `docs/plans/` overlapping this area:**
`session-recovery-observation-audit.md` (detect-only index drift, #2086) —
complementary, no overlap in the code this plan edits.

**Notes:** Keyspace already clean; this is pure code hardening, no data work.

## Prior Art

- **PR #2102 (#2101)**: "Fix AgentSession pending-index phantom leak — A1 rebuild
  guard." Installed the transient `status.on_save` shim inside `repair_indexes()`
  that skips the SADD for identity-less hashes during rebuild. **Succeeded for
  `status`, but scoped to one field** — it left the other three IndexedFields
  un-guarded and never touched the raw-`rebuild_indexes()` worker Step 1 path.
  This plan is the direct generalization.
- **PR #1078 (#1069)**: "Fix agent-session-cleanup phantom-record destruction."
  Made the cleanup pass phantom-aware. Complementary; not the rebuild path.
- **Issues #1459, #2086**: class-set orphan cleanup (`clean_indexes`, SSCAN) and
  detect-only drift reconciliation. Orthogonal — those handle gone-hash orphans,
  not identity-less-hash re-inflation.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #2102 (A1 guard) | Shimmed `status.on_save` to skip identity-less hashes during `repair_indexes()` rebuild | Scoped to a single field. `task_type`, `claude_session_uuid`, `claude_pid` remained un-guarded and kept re-inflating. Also never covered the worker Step 1 `run_cleanup` path, which calls raw `rebuild_indexes()` (no guard at all). |

**Root cause pattern:** the fix was field-specific (`status`) when the defect is
field-agnostic — *any* IndexedField's `on_save` re-inflates when run against an
identity-less hash whose decode regenerates the AutoKey. The correct altitude is
"skip identity-less instances for **all** IndexedFields," ideally at the
per-instance level, on **every** rebuild entry point. **The durability of this
"derived from `cls._meta`" thesis must be verified by a runtime assertion, not
just asserted in prose** (see critique CONCERN #2 and the Test Impact section) —
otherwise a builder can hardcode the four names and reproduce PR #2102's exact
field-specific miss while passing CI.

## Data Flow

1. **Entry point A — worker startup:** `worker/__main__.py` Step 1 →
   `run_cleanup()` → per-model rebuild thread → popoto `rebuild_indexes` per-field
   `on_save` loop → `INDEX_SWAP_LUA` HSET.
2. **Entry point B — hourly reflection / recovery:** `agent-session-cleanup`
   reflection (`session_health.py:4954`), `session_pickup.py:452,612`,
   `agent_session_queue.py` reaper, and `scripts/update/run.py:1818` all call
   `AgentSession.repair_indexes()` → (A1-guarded) `rebuild_indexes`.
3. **Re-inflation:** for an identity-less hash, `decode_popoto_model_hashmap`
   regenerates the AutoKey uuid; `on_save` for each IndexedField issues
   `INDEX_SWAP_LUA` with the new key as `KEYS[1]`, HSETting the field + its
   `\x00idxset` pointer → a fresh phantom hash.
4. **Output (fixed):** identity-less instances are skipped before any IndexedField
   `on_save` runs, on both entry points → no new phantom hashes.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none public. The generalized A1 guard stays internal to
  `AgentSession.repair_indexes`; `run_cleanup`'s timeout mechanism changes
  internally but keeps its return-dict contract.
- **Coupling:** slightly *reduces* it — the guard becomes field-list-driven
  (derived from `cls._meta` IndexedFields) rather than hardcoding `status`.
- **Data ownership:** unchanged. Still ORM-only, no raw-Redis writes on Popoto
  keys.
- **Reversibility:** high — both changes are localized and revert cleanly.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the "skip identity-less for all IndexedFields"
  altitude and the Step-1 bounding approach)
- Review rounds: 1

## Prerequisites

No prerequisites — the keyspace is already clean and this work has no external
dependencies. (Redis running locally is required for the integration test, which
is standard for this repo's test suite.)

## Solution

### Key Elements

- **Generalized A1 guard**: extend the identity-less skip in
  `AgentSession.repair_indexes` from `status` alone to **every** IndexedField on
  the model (`status`, `task_type`, `claude_session_uuid`, `claude_pid`), derived
  at runtime from `cls._meta` so a future IndexedField is covered automatically.
  The install/restore lifecycle is hardened per the critique: shims installed
  inside the `try` (no partial-install leak), a non-reentrant lock around the
  whole install→rebuild→restore, and a re-entrancy abort guard.
- **Guard the worker Step 1 path**: stop worker Step 1 from re-inflating
  AgentSession. Route AgentSession through the guarded `repair_indexes()` instead
  of raw `rebuild_indexes()` — cleanest by excluding `AgentSession` from
  `run_cleanup`'s generic sweep (like `Reflection` already is), because worker
  Step 2 (`cleanup_corrupted_agent_sessions`, `session_health.py:4954`) already
  calls the guarded `repair_indexes()` unconditionally. Step 1's raw AgentSession
  rebuild is therefore both redundant and dangerous. **This exclusion is the
  primary fix.**
- **Minimal, interpreter-exit-safe Step 1 un-wedge**: fix `run_cleanup` so a
  per-model timeout can never block the serve loop — run each rebuild on a daemon
  thread joined with a timeout, so an overrunning rebuild is abandoned (never
  `shutdown(wait=True)`-joined) and never blocks interpreter shutdown. A poisoned
  or slow model is skipped; the worker proceeds and heartbeats within threshold.
- **Monitored "re-inflation stopped" prod signal**: wire the quarantine counter
  and per-model keyspace deltas into observable surfaces so the fix is verifiable
  in production, not just in unit tests (critique CONCERN #6).

### Flow

Worker boots → Step 1 `run_cleanup` sweeps non-AgentSession models, each on a
daemon thread under an enforced per-model budget (abandons any that overrun) →
AgentSession is handled by the A1-guarded `repair_indexes()` in Step 2 →
identity-less hashes skipped for all IndexedFields → zero phantom re-inflation →
worker heartbeats within threshold and begins serving.

### Technical Approach

- **Generalize the guard (`models/agent_session.py:2168-2203`).** Replace the
  single-field `status_field` shim with a loop that installs the same
  identity-less-skip wrapper on the `on_save` of every IndexedField enumerated at
  runtime from `cls._meta` (keep the existing `_filter_hydrated_sessions([instance])`
  identity check — it is the canonical test). Preserve the transient-shim design
  (guard only for the duration of the `rebuild_indexes()` call; live `save()`
  stays unguarded so a genuine brand-new session still indexes to `:pending`).
  Keep the 2-tuple return arity and the `_last_quarantined_identityless` counter
  (now summed across fields). The install/restore lifecycle carries three
  hardening requirements from the critique:
  - **Install shims INSIDE the `try` (critique CONCERN #1).** A partial install
    (a later field's assignment raises) must never leak a shim. Structure:
    ```python
    indexed_fields = [f for _, f in cls._meta.fields.items() if isinstance(f, IndexedField)]
    try:
        for f in indexed_fields:
            orig = f.__dict__.get("on_save")  # capture per-field, in the closure
            f.on_save = _make_identityless_skip_shim(f, orig)
        rebuilt = cls.rebuild_indexes()
    finally:
        for f in indexed_fields:            # drive restore from the FULL list, not "fields observed installed"
            f.__dict__.pop("on_save", None)  # existing guarded-del pattern, per field
    ```
    Capture each field's original `on_save` **per-field inside the closure**,
    never a shared variable across the loop.
  - **Guard against re-entrancy with a non-reentrant lock (critique CONCERN #5).**
    The shim mutates process-global objects (`cls._meta.fields[...].on_save`) and
    generalizing to 4 fields widens the single-threaded-rebuild window 4×. Wrap
    install→`rebuild_indexes()`→restore in a class-level
    `cls._repair_lock = threading.Lock()`; acquire with `blocking=False` and, on
    failure, skip the rebuild + log a WARNING (do not queue). Additionally, before
    capturing a field's original `on_save`, assert `"on_save" not in f.__dict__`
    (no live shim) — if one is already present, abort rather than snapshot a shim
    as the "original," which would make a `finally` `del` un-shim prematurely.
- **Exclude AgentSession from `run_cleanup`'s generic sweep**
  (`scripts/popoto_index_cleanup.py`). Add `"AgentSession"` to the existing
  `_SCHEDULER_STATE_MODELS`-style exclusion (or a new, clearly-named
  `_GUARDED_ELSEWHERE` frozenset) with a comment: AgentSession is rebuilt via the
  A1-guarded `AgentSession.repair_indexes()` in worker Step 2 and the hourly
  reflection; a raw `rebuild_indexes()` here re-inflates identity-less phantoms.
  **This exclusion is the real fix** — AgentSession is the only known degenerate
  keyspace, so removing it from Step 1 removes the trigger for the wedge entirely.
- **Make the Step 1 un-wedge minimal AND interpreter-exit-safe (critique
  CONCERNs #3 and #4)** (`scripts/popoto_index_cleanup.py:213-227`). Do not use
  `with ThreadPoolExecutor(...) as executor:` — its `__exit__` calls
  `shutdown(wait=True)`, the actual 8h wedge. Do **not** substitute
  `ThreadPoolExecutor.shutdown(wait=False)` either: a `ThreadPoolExecutor` worker
  is non-daemon and is still joined at interpreter exit via
  `concurrent.futures.thread._python_exit`, so it does not satisfy Risk 2's "never
  blocks interpreter shutdown." Instead run each model's rebuild on a bare
  `threading.Thread(target=rebuild, daemon=True)`, `join(timeout=_REBUILD_TIMEOUT_SECONDS)`;
  on timeout log a WARNING naming the model and continue the sweep — the daemon
  thread is abandoned and never blocks interpreter shutdown. This is the minimal
  un-wedge (no pool, no lifecycle to manage): the daemon-thread abandon is now a
  rare defense-in-depth safety net, because the AgentSession exclusion already
  removes the only known trigger. A robust *general* per-model budget (iteration
  caps, cross-model scheduling) is explicitly out of scope for this slug — see
  No-Gos. Keep the per-model try/except and the summary dict. `_REBUILD_TIMEOUT_SECONDS`
  is a named, env-overridable constant (grain-of-salt: provisional, tunable).
- **Wire a monitored "re-inflation stopped" prod signal (critique CONCERN #6).**
  Every other Success Criterion is a unit-test/lint/grep — none confirms
  re-inflation actually stopped in production. Two cheap wires close that gap:
  1. Have `run_cleanup` record a per-model keyspace delta (`scard` of the class
     set and/or `dbsize` before-vs-after each model's rebuild) into the returned
     summary dict, which is already logged at worker startup — so a degenerate
     model whose abandoned daemon thread re-inflates unbounded becomes visible in
     the worker log rather than silent.
  2. Wire `AgentSession._last_quarantined_identityless` (now summed across all
     four fields) into the `tools.doctor agentsession-index-drift` check with a
     nonzero-warns threshold, so "re-inflation stopped" is a standing prod signal,
     not just a green unit test.
- **Popoto is pip-installed (1.8.0), not vendored** — the fix lives entirely in
  repo code as a transient shim, consistent with the existing A1 pattern. No
  upstream popoto change.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_cleanup`'s per-model `try/except` swallows and logs at WARNING — assert
  a poisoned/slow model produces the WARNING and the sweep continues to the next
  model (observable behavior, not silent pass).
- [ ] The generalized guard's `finally` restore must run even if
  `rebuild_indexes()` raises — assert original `on_save` is restored on every
  wrapped field after an exception (no leaked shim — critique CONCERN #1).

### Empty/Invalid Input Handling
- [ ] Identity-less hash (all key fields None) — assert it is skipped for every
  IndexedField (the core fix). Covered by the extended
  `test_repair_does_not_reinflate_from_identityless_hashes`.
- [ ] Empty keyspace — `repair_indexes()` and `run_cleanup()` return cleanly
  (no-op), no exception.

### Durability of the field-agnostic thesis (critique CONCERN #2)
- [ ] Assert the guarded field set is **computed at runtime from `cls._meta`** and
  equals the live IndexedField set — do NOT hardcode the four names, or a builder
  who hardcodes them passes CI while reproducing PR #2102's exact field-specific
  miss. Concretely:
  `assert set(guarded_fields) == {n for n, f in AgentSession._meta.fields.items() if isinstance(f, IndexedField)}`.
  This fails closed the moment a 5th IndexedField is added without touching the
  guard, which is the whole point of the `cls._meta`-derived design.

### Concurrency / Re-entrancy (critique CONCERN #5)
- [ ] A second `repair_indexes()` entered while the shims are installed is skipped
  via the non-reentrant `_repair_lock` (fails `acquire(blocking=False)`), logs a
  WARNING, and does not snapshot a live shim as an "original."

### Error State Rendering
- [ ] Worker Step 1 timeout path logs a visible WARNING naming the model; assert
  it appears and that startup proceeds (heartbeat continues). No user-facing
  surface — this is a bridge/worker-internal change.

## Test Impact

- [ ] `tests/unit/test_agentsession_pending_index_leak.py::test_repair_does_not_reinflate_from_identityless_hashes`
  — UPDATE: extend from `status`-only to assert **no re-inflation across all four
  IndexedFields** (`status`, `task_type`, `claude_session_uuid`, `claude_pid`);
  seed identity-less hashes carrying each field's `\x00idxset` pointer and assert
  `dbsize` does not grow across a `repair_indexes()` pass. ADD a companion
  assertion that the guarded field set is computed at runtime and equals the live
  `cls._meta` IndexedField set (critique CONCERN #2 — no hardcoded four-name list).
- [ ] `tests/unit/test_agentsession_pending_index_leak.py` — ADD: after
  `repair_indexes()` raises mid-rebuild, assert every IndexedField's original
  `on_save` is restored (no leaked shim — critique CONCERN #1); and that a
  re-entrant `repair_indexes()` while shims are installed is skipped via
  `_repair_lock` (critique CONCERN #5).
- [ ] `tests/unit/test_worker_entry.py` — UPDATE/ADD: assert AgentSession is
  excluded from `run_cleanup`'s model set; that a model whose `rebuild_indexes`
  overruns the budget is abandoned (the call returns without blocking on the
  still-running daemon thread) so worker startup proceeds — asserting only the
  observable contract (WARNING naming the model + sweep continues), not the
  concurrency mechanism; and that `run_cleanup`'s summary dict carries the
  per-model keyspace delta (critique CONCERN #6).
- [ ] `tests/unit/test_session_health_phantom_guard.py` — REVIEW (likely no
  change): it asserts `repair_indexes` runs unconditionally; confirm the
  multi-field guard does not alter its expectations.

## Rabbit Holes

- **Re-litigating the raw-Redis purge exception.** Already resolved by the shipped
  ORM-compliant `scripts/purge_phantom_agent_sessions.py`. Do not touch it.
- **Chasing the historical producer (#2204 catchup / MISCONF retries).** The
  producer is understood and the acute flood is purged; this plan fixes the
  re-inflation + wedge mechanism only. Producer hardening is separately tracked.
- **Reimplementing popoto's `rebuild_indexes` loop in-repo.** Tempting for a
  per-instance skip, but fragile against popoto upgrades. Stay with the transient
  per-field `on_save` shim — same proven pattern as the existing A1 guard.
- **Rewriting `run_cleanup` into an async/background subsystem, or building a
  robust general per-model budget** (iteration caps, cross-model scheduling). Out
  of appetite. The minimal fix is: don't join the overrunning thread (daemon +
  join-timeout), and don't rebuild AgentSession here. If a robust general budget
  is genuinely wanted later, split it to its own issue (critique CONCERN #4).

## Risks

### Risk 1: A wrapped field's `on_save` is not restored after an exception
**Impact:** a leaked shim would suppress legitimate indexing for that field on
live saves (inverse bug — a real new session fails to index).
**Mitigation:** install shims *inside* the `try`; restore every wrapped field in
the `finally`, driven from the full enumerated IndexedField list with a per-field
guarded `del` (mirror the existing single-field pattern); add a test asserting all
originals are restored after `rebuild_indexes()` raises.

### Risk 2: Abandoned rebuild thread keeps consuming CPU/Redis after timeout
**Impact:** a truly wedged rebuild thread lingers until process exit.
**Mitigation:** with the guard fix, AgentSession (the only known degenerate case)
no longer rebuilds in Step 1 at all, so the abandoned-thread path becomes a rare
safety net rather than the norm. The abandon runs on a bare
`threading.Thread(target=rebuild, daemon=True)` (NOT a `ThreadPoolExecutor` —
a pool worker is non-daemon and is joined at interpreter exit via
`concurrent.futures.thread._python_exit`, so `shutdown(wait=False)` would not
prevent the exit-time block). Because the thread is a daemon, it never blocks
interpreter shutdown.

### Risk 3: Excluding AgentSession from Step 1 leaves a real orphan window
**Impact:** class-set/KeyField orphans could go un-rebuilt at Step 1.
**Mitigation:** Step 2 (`cleanup_corrupted_agent_sessions`) already calls the
guarded `repair_indexes()` unconditionally, and Step 2b `clean_indexes()` sweeps
class-set orphans — AgentSession index hygiene is fully covered downstream.

## Race Conditions

### Race 1: transient `on_save` shim assumes single-threaded rebuild
**Location:** `models/agent_session.py:2168-2212`
**Trigger:** a concurrent `AgentSession(...).save()` on another thread while the
shim is installed would hit the guarded `on_save`.
**Data prerequisite:** none.
**State prerequisite:** rebuild runs single-threaded in its actual call contexts
(worker startup Step 2, hourly reflection tick, update script) — the same
assumption the existing A1 guard already relies on and documents.
**Mitigation:** preserve the existing single-threaded-rebuild assumption and its
docstring note; do not broaden the shim's temporal scope. The guard only *skips*
work for identity-less hashes, so even a concurrent healthy save delegates to the
original `on_save` unchanged.

### Race 2: two `repair_indexes()` calls overlap in-process (window widened 4×)
**Location:** `models/agent_session.py:2168-2212`
**Trigger:** generalizing the shim to 4 fields widens the mutate-process-global
window 4×. If a second `repair_indexes()` runs while the first's shims are
installed, the second could snapshot the first's shim as the "original," and the
first `finally`'s `del` would un-shim prematurely (inverse bug: legitimate
indexing suppressed).
**Data prerequisite:** none.
**State prerequisite:** two overlapping in-process rebuilds.
**Mitigation (critique CONCERN #5):** a non-reentrant
`cls._repair_lock = threading.Lock()` wraps install→`rebuild_indexes()`→restore; a
second concurrent caller fails `acquire(blocking=False)`, skips, and logs a
WARNING. Belt-and-braces: each field's original `on_save` capture asserts
`"on_save" not in field.__dict__` first, so a re-entrant call can never snapshot a
live shim as the original.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2204] Fixing the historical *producer* of the churn (catchup
  re-handling / re-enqueue loop) — tracked on its own issue; this plan fixes the
  re-inflation + startup-wedge mechanism only.
- **A robust general per-model rebuild budget** (iteration caps, N×-class-set-
  cardinality abort, cross-model scheduling). The minimal daemon-thread +
  join-timeout un-wedge is sufficient for this slug; a deeper general budget is a
  separate issue if genuinely wanted (resolves Open Question 1 & 2 and critique
  CONCERN #4).
- Nothing else deferred — the generalized guard, the worker Step 1 exclusion, the
  minimal un-wedge, and the observability wires are all in scope for this plan.

## Update System

No update system changes required — this is a pure code change to
`models/agent_session.py`, `scripts/popoto_index_cleanup.py`, and
`worker/__main__.py` behavior. The one-off `/update` migration
`purge_phantom_agent_sessions` already ships (self-cleans other machines on next
update) and is unchanged by this plan. No new deps, config, or migration.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to index
rebuild and worker startup. No new CLI entry point, MCP surface, or bridge import.
The worker already runs `run_cleanup` and `repair_indexes` at startup; behavior is
corrected in place.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/popoto-index-hygiene.md` — document the generalized
  (all-IndexedField) A1 guard, the install-inside-`try` + non-reentrant-lock
  hardening, the AgentSession exclusion from `run_cleanup`, the minimal
  daemon-thread abandon-on-timeout semantics of Step 1, and the doctor/summary
  observability wires.
- [ ] Update the A1-guard docstring in `models/agent_session.py:2112-2143` to
  describe the multi-field guard, the install-inside-`try` invariant, and the
  re-entrancy lock (currently says "only status is an IndexedField here").

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site for this area.

### Inline Documentation
- [ ] Comment the `run_cleanup` AgentSession exclusion explaining the
  re-inflation hazard.
- [ ] Comment the daemon-thread + join-timeout path explaining why the old
  `with`-context-manager join (and a `ThreadPoolExecutor` more generally) was the
  wedge.

## Success Criteria

- [ ] `AgentSession.repair_indexes()` re-inflates zero phantoms across all four
  IndexedFields (extended `test_repair_does_not_reinflate_from_identityless_hashes`
  passes; `dbsize` stable across a rebuild pass over seeded identity-less hashes).
- [ ] The guarded field set is computed at runtime and equals the live `cls._meta`
  IndexedField set (runtime set-equality assertion — no hardcoded four-name list;
  critique CONCERN #2).
- [ ] AgentSession is excluded from `run_cleanup`'s generic model sweep; grep
  confirms the exclusion and a comment explains it.
- [ ] Worker Step 1 no longer blocks on an overrunning per-model rebuild (no
  `ThreadPoolExecutor`/`shutdown(wait=True)` on the critical path; daemon thread +
  join-timeout); a slow model is abandoned and startup proceeds.
- [ ] Worker startup completes and heartbeats within its normal threshold (360s)
  on a degenerate keyspace (verified via the worker-entry test's timeout path).
- [ ] `run_cleanup`'s summary dict records per-model keyspace deltas, and
  `AgentSession._last_quarantined_identityless` is wired into the doctor
  `agentsession-index-drift` check (critique CONCERN #6).
- [ ] `python -m tools.doctor` `agentsession-index-drift` check passes (asserted by
  parsing the `--json` status, not merely the check name's presence).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (index-guard)**
  - Name: `index-guard-builder`
  - Role: Generalize the A1 guard to all IndexedFields in
    `AgentSession.repair_indexes` (install-inside-`try`, non-reentrant lock,
    re-entrancy abort); update docstring.
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Builder (worker-step1)**
  - Name: `worker-step1-builder`
  - Role: Exclude AgentSession from `run_cleanup`; replace the blocking
    context-manager join with a daemon thread + join-timeout; record per-model
    keyspace deltas into the summary dict.
  - Agent Type: builder
  - Domain: async-concurrency
  - Resume: true

- **Test Engineer (rebuild-tests)**
  - Name: `rebuild-test-engineer`
  - Role: Extend the identity-less re-inflation test to all four fields; add the
    runtime set-equality, shim-restore-after-raise, re-entrancy-skip, worker Step 1
    exclusion, budget-abandon, and summary-delta tests.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Verify all success criteria and Verification rows.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Generalize the A1 guard to all IndexedFields
- **Task ID**: build-index-guard
- **Depends On**: none
- **Validates**: tests/unit/test_agentsession_pending_index_leak.py, tests/unit/test_session_health_phantom_guard.py
- **Assigned To**: index-guard-builder
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: true
- In `models/agent_session.py:2168-2203`, replace the single `status_field` shim
  with a loop that wraps `on_save` for every IndexedField enumerated at runtime
  from `cls._meta` (currently `status`, `task_type`, `claude_session_uuid`,
  `claude_pid` — but never hardcode the list), reusing
  `_filter_hydrated_sessions([instance])` as the identity check.
- **Install the shims INSIDE the `try`** so a partial install can never leak
  (critique CONCERN #1); drive the `finally` restore from the full enumerated
  IndexedField list (not "fields observed installed"), each with the existing
  guarded `del`. Capture each field's original `on_save` per-field inside the
  closure, never a shared variable.
- **Wrap install→`rebuild_indexes()`→restore in a non-reentrant
  `cls._repair_lock = threading.Lock()`** (critique CONCERN #5); on
  `acquire(blocking=False)` failure, skip + WARNING. Before capturing a field's
  original `on_save`, assert `"on_save" not in field.__dict__` (abort if a shim is
  already live) so a re-entrant call cannot snapshot a shim as the original.
- Sum `_last_quarantined_identityless` across fields. Preserve the 2-tuple return.
- Update the docstring (`:2112-2143`) to describe the multi-field guard, the
  install-inside-`try` invariant, and the re-entrancy lock.

### 2. Exclude AgentSession from run_cleanup + minimal Step 1 un-wedge
- **Task ID**: build-worker-step1
- **Depends On**: none
- **Validates**: tests/unit/test_worker_entry.py
- **Assigned To**: worker-step1-builder
- **Agent Type**: builder
- **Domain**: async-concurrency
- **Parallel**: true
- In `scripts/popoto_index_cleanup.py`, exclude `AgentSession` from the generic
  sweep (add to the exclusion frozenset with an explanatory comment: rebuilt via
  the guarded `repair_indexes()` in worker Step 2 / hourly reflection). **This is
  the primary fix.**
- Replace the `with ThreadPoolExecutor(...) as executor:` block with a bare
  `threading.Thread(target=rebuild, daemon=True)` + `join(timeout=_REBUILD_TIMEOUT_SECONDS)`
  (critique CONCERNs #3/#4): on timeout, log a WARNING naming the model and
  continue the sweep; the daemon thread is abandoned and never blocks interpreter
  shutdown. Do NOT use `ThreadPoolExecutor.shutdown(wait=False)` — a pool worker is
  non-daemon and is joined at interpreter exit. Name `_REBUILD_TIMEOUT_SECONDS` as
  an env-overridable constant (provisional/tunable). Keep it minimal — no general
  budget subsystem (out of scope; see No-Gos).
- Record a per-model keyspace delta (`scard`/`dbsize` before-vs-after) into
  `run_cleanup`'s returned summary dict for prod visibility (critique CONCERN #6).

### 3. Extend + add tests
- **Task ID**: build-tests
- **Depends On**: build-index-guard, build-worker-step1
- **Assigned To**: rebuild-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Extend `test_repair_does_not_reinflate_from_identityless_hashes` to seed
  identity-less hashes with each of the four IndexedFields' `\x00idxset` pointers
  and assert zero re-inflation (`dbsize` stable) across a `repair_indexes()` pass.
- Add the runtime-`_meta` set-equality assertion (critique CONCERN #2):
  `set(guarded_fields) == {n for n, f in AgentSession._meta.fields.items() if isinstance(f, IndexedField)}`
  — no hardcoded four-name list, fails closed on a 5th IndexedField.
- Add a test asserting all wrapped `on_save` originals are restored after
  `rebuild_indexes()` raises (no leaked shim — critique CONCERN #1).
- Add a test asserting a re-entrant `repair_indexes()` (shim already installed) is
  skipped via `_repair_lock` (critique CONCERN #5).
- Add a test asserting AgentSession is absent from `run_cleanup`'s model set.
- Add a test asserting a model whose `rebuild_indexes` overruns the budget is
  abandoned (call returns without blocking on the daemon thread) and the sweep
  continues, asserting only the observable contract (WARNING + continue).
- Add a test asserting `run_cleanup`'s summary dict carries per-model keyspace
  deltas (critique CONCERN #6).

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-index-guard, build-worker-step1, build-tests
- **Assigned To**: rebuild-test-engineer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/popoto-index-hygiene.md` per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-index-guard, build-worker-step1, build-tests, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows and confirm every success criterion, including the
  `--json`-parsed doctor status assertion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Re-inflation test passes | `pytest tests/unit/test_agentsession_pending_index_leak.py -q` | exit code 0 |
| Worker entry tests pass | `pytest tests/unit/test_worker_entry.py -q` | exit code 0 |
| Phantom guard tests pass | `pytest tests/unit/test_session_health_phantom_guard.py -q` | exit code 0 |
| AgentSession excluded from run_cleanup sweep | `grep -n "AgentSession" scripts/popoto_index_cleanup.py` | output contains AgentSession |
| No blocking context-manager join on rebuild | `grep -n "with concurrent.futures.ThreadPoolExecutor" scripts/popoto_index_cleanup.py` | match count == 0 |
| No ThreadPoolExecutor at all in the sweep | `grep -n "ThreadPoolExecutor" scripts/popoto_index_cleanup.py` | match count == 0 |
| Doctor index-drift check actually passes | `python -m tools.doctor --json \| python -c "import json,sys; d=json.load(sys.stdin); c=[x for x in d.get('checks',d) if 'agentsession-index-drift' in str(x)]; sys.exit(0 if c and all(str(x.get('status','')).lower() in ('pass','passing','ok') for x in c) else 1)"` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), 2026-07-23. Verdict: READY TO BUILD (with concerns). Concerns folded into the plan body by the /do-plan revision pass, 2026-07-23. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Adversary) | A 4-field shim install-loop placed *before* the `try` leaks 1-3 shims if a later field's assignment raises during install — the `finally` never covers a partial install, permanently suppressing `:pending` indexing (inverse bug). | Task 1 | Install shims INSIDE the `try`; drive the `finally` restore from the full enumerated IndexedField list (not just fields observed installed), each with the existing guarded `del`: `try: for f in indexed_fields: f.on_save = make_shim(f, f.on_save); rebuilt = cls.rebuild_indexes() finally: for f in indexed_fields: f.__dict__.pop("on_save", None)`. Capture each field's `orig_on_save` per-field inside the closure, not a shared variable. |
| CONCERN | History & Consistency (Archaeologist) | The durability thesis ("derived from `cls._meta` so a future IndexedField is covered automatically") is never verified. Every test/criterion pins the four current field names, so a builder who hardcodes them passes CI while reproducing PR #2102's exact field-specific-miss failure — the root cause this plan claims to fix. | Task 3 | Add an assertion that the guarded set is computed at runtime and equals the live set: `assert set(guarded_fields) == {n for n, f in AgentSession._meta.fields.items() if isinstance(f, IndexedField)}` — fails closed when a 5th IndexedField is added without touching the guard. |
| CONCERN | History & Consistency (Consistency Auditor) | Technical Approach lists `ThreadPoolExecutor.shutdown(wait=False)` and "a daemon thread" as interchangeable, but they are not: a `ThreadPoolExecutor` worker is non-daemon and is joined at interpreter exit via `concurrent.futures.thread._python_exit`, so `shutdown(wait=False)` does NOT satisfy Risk 2's "never blocks interpreter shutdown." | Task 2 | Use a bare `threading.Thread(target=rebuild, daemon=True)`, `join(timeout=_REBUILD_TIMEOUT_SECONDS)`, on timeout log the WARNING and continue — and update Risk 2 to reference that daemon thread, not a pool. Drop the `ThreadPoolExecutor+shutdown(wait=False)` alternative. |
| CONCERN | Scope & Value (Simplifier) | The plan ships two independent wedge fixes — exclude AgentSession from `run_cleanup` AND a full abandon-on-timeout thread rewrite. Once AgentSession (the only known degenerate keyspace) and EmbeddingField models are excluded, only cheap continuously-indexed models remain, so the abandon machinery defends a trigger the exclusion just removed (Open Question 1). Three critics converged on the Step 1 change. | Task 2 / Open Question 1 | Keep the exclusion (the real fix). Shrink the budget change to the minimum un-wedge: replace the blocking `with`-context-manager join with a daemon thread + join-timeout so `shutdown(wait=True)` is never called (the existing `except TimeoutError: ... continue` already logs and moves on) — minimal, no pool lifecycle. A robust general budget is split to its own issue. |
| CONCERN | Risk & Robustness (Skeptic) | The guard mutates a process-global object (`cls._meta.fields[...].on_save`) and rests on the asserted-not-proven "single-threaded rebuild" assumption; generalizing to 4 fields widens the window 4x. If two `repair_indexes()` calls ever overlap in-process, the second snapshots the first's shim as `orig_on_save` and the first `finally`'s `del` un-shims prematurely. | Task 1 | Wrap install→`rebuild_indexes()`→restore in a non-reentrant `cls._repair_lock = threading.Lock()`; on `acquire(blocking=False)` failure skip + WARNING. Do NOT capture `orig_on_save` if `"on_save" in field.__dict__` already (a shim is live) — assert-and-abort so a re-entrant call cannot snapshot a shim as the original. |
| CONCERN | Risk & Robustness (Operator) | No Success Criterion or monitor confirms re-inflation actually stopped in prod — all criteria are unit-test/lint/grep. A degenerate non-AgentSession model's abandoned daemon thread would re-inflate unbounded with zero visibility, re-spawned each worker restart. | Task 3 / doctor | Have `run_cleanup` record per-model `scard`/keyspace delta before-vs-after into the returned summary dict (already logged at worker startup), and wire `AgentSession._last_quarantined_identityless` into the `tools.doctor agentsession-index-drift` check with a nonzero-warns threshold, so "re-inflation stopped" is a monitored prod signal. |
| NIT | History & Consistency | Success Criteria says the doctor `agentsession-index-drift` check "passes," but the Verification row only asserts `output contains agentsession-index-drift` — presence of the check name does not prove it passed. | Task 5 | Pipe `--json` through a `python -c` that reads the `agentsession-index-drift` entry and exits non-zero unless its status is passing. |
| NIT | Scope & Value (Simplifier) | The plan over-specifies the concurrency mechanism (`shutdown(wait=False)`/daemon) that the async-concurrency builder should own; the success-criterion grep already pins the observable contract. | Task 2 | State the contract only ("on per-model timeout, log a WARNING naming the model and let the sweep proceed without joining the rebuild thread") and let the builder pick the mechanism. Covered by CONCERN #3's resolution. |

**Revision disposition (all folded into the plan body):** #1 → Technical Approach
(install-inside-`try`) + Task 1 + Risk 1. #2 → Failure Path Test Strategy
(durability assertion) + Test Impact + Task 3 + Success Criteria. #3 → Technical
Approach (daemon thread) + Risk 2 + Task 2. #4 → Technical Approach (minimal
un-wedge) + No-Gos + Task 2 (resolves Open Questions 1 & 2). #5 → Technical
Approach (non-reentrant lock) + Race 2 + Task 1. #6 → Technical Approach
(observability wires) + Success Criteria + Task 2/3. Both NITs → Verification row
(`--json`-parsed doctor status) and observable-contract-only test framing.
