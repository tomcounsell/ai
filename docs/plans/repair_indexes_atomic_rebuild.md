---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-18
tracking: https://github.com/tomcounsell/ai/issues/1720
last_comment_id:
---

# repair_indexes() Non-Atomic Rebuild — Investigation & Atomic Remediation

## Problem

`AgentSession.repair_indexes()` (`models/agent_session.py:2051-2077`) clears every
`$IndexF:AgentSession:*` index key (including `$IndexF:AgentSession:session_id:*`) and
only **afterward** calls `rebuild_indexes()` to repopulate them. During the gap between
the destructive `DELETE` loop and the repopulate, a concurrent
`AgentSession.query.filter(session_id=...)` returns empty.

This is exactly what `valor-session status --id <id>` does
(`tools/valor_session.py:613`), and what worker recovery, steering delivery, and the SDLC
stage-query path do. The original symptom (`yudame/cuttlefish#512` / this repo's #496 run):
a freshly-created `AgentSession` was unretrievable shortly after creation —
`valor-session status --id <id>` → `Session not found` — breaking stage-by-stage dispatch.

`repair_indexes()` runs **hourly** (the `agent-session-cleanup` reflection,
`agent/session_health.py:2626`) and at **worker startup** (`agent/session_pickup.py:411`).
So any reader polling a recently-created session during the rebuild window can still hit a
transient `Session not found`.

**Current behavior:** A concurrent reader during the ~clear→rebuild window gets an empty
result from `query.filter(session_id=...)` and reports `Session not found` for a session
that exists and is valid. Self-healing (next read after rebuild succeeds), but a real flake.

**Desired outcome:** First, *quantify* the window and the lookup-failure probability and
*confirm* the dominant source of stale `$IndexF` members (the investigation the issue asks
for). Then, if the data supports it, ship the lowest-risk remediation the issue sanctions:
make the rebuild non-destructive to concurrent readers (no observable empty-window), and/or
add a short bounded retry on the critical `session_id` read paths.

## Freshness Check

**Baseline commit:** `87e5a26a` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-06-17T10:22:05Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `models/agent_session.py:2044-2051` (issue) — **drifted** to `models/agent_session.py:2051-2077`.
  The mechanism is unchanged: the loop at line 2069-2074 iterates `$IndexF:AgentSession:*`,
  counts phantoms, then `POPOTO_REDIS_DB.delete(index_key)` deletes the whole key; line 2076
  calls `rebuild_indexes()` afterward. Non-atomic clear→rebuild confirmed.
- `agent/agent_session_queue.py:324` (`_push_agent_session` calls `async_create` synchronously) —
  still holds (record durably written at create time; queue-only hypothesis stays disproven).
- `tools/valor_session.py:420` (child-session refusal) — still holds (PM/Dev collapse, #1633).
- `tools/valor_session.py` lookup helper — `query.filter(session_id=...)` at line 613, no retry. Confirmed.
- `models/agent_session.py:529-530` — `Meta.ttl = 2592000` (30 days) confirmed.

**Cited sibling issues/PRs re-checked:**
- #1361 — CLOSED. Removed the gate so `repair_indexes()` runs unconditionally every tick.
- #1459 — CLOSED. Orphan index cleanup / Sentry-noise remediation.
- #1271 — CLOSED. Orphan process reaping in the cleanup reflection.
- #1006 — CLOSED. Killed-session resurrection fix; introduced the `repair_indexes()` call in pickup.
- #496 / `cuttlefish#512` — original symptom source, already re-scoped into this issue.

**Commits on main since issue was filed (touching referenced files):**
- `995bc453 fix(granite): correct transcript path slug` — **irrelevant** (granite transcript
  path, not index code). No commits touched `repair_indexes()` itself.

**Active plans in `docs/plans/` overlapping this area:** none. (`never_started_session_recovery.md`
and `granite_lossless_checkpoint_resume.md` touch session lifecycle but not the index-rebuild path.)

**Notes:** Line drift only; root cause intact. Corrected file:line is `models/agent_session.py:2051-2077`.
A deeper finding surfaced during freshness verification (see Research / Architectural Impact):
popoto's own `rebuild_indexes()` (`.venv/.../popoto/models/base.py:2707`) ALSO deletes all
secondary index keys before reconstructing them — so the destructive window is actually *two*
nested clear→rebuild passes, and the inner popoto deletes are outside this model's control.
This materially shapes which remediation options are viable.

## Prior Art

- **#1361 (CLOSED)**: Removed the gate that prevented `repair_indexes()` from flushing genuine
  drift — made it run unconditionally every cleanup tick. This is *why* the window is now hit
  hourly rather than rarely; the fix that increased correctness also increased window exposure.
- **#1459 (CLOSED)**: Redis orphan index cleanup causing 28k+ Sentry events. Established the
  `_filter_hydrated_sessions` phantom-drop-on-read mitigation. Relevant: read-side phantom
  filtering already exists, but it does not help the *empty-window* case (the index key is gone,
  not pointing at a dead hash).
- **#1006 (CLOSED)**: Killed sessions resurrecting in the running index. Introduced the
  `repair_indexes()` call at `agent/session_pickup.py:411` (chose it over `rebuild_indexes()`).
- **#1069 (CLOSED)**: agent-session-cleanup destroying valid sessions via phantom misclassification.
  Cautionary prior art: changes to the cleanup/rebuild path have a history of collateral damage —
  any remediation must be conservative and well-tested.
- **#1335 (CLOSED)**: Index-staleness for `waiting_for_children` sessions. Confirms TTL/index
  desync as a recurring real source of stale members.

No prior PR attempted to make `repair_indexes()` atomic. This is the first pass at the window itself.

## Research

External research skipped for the mechanism (purely internal Popoto/Redis behavior), but the
popoto library internals were read directly as the authoritative source:

**Key findings (from reading `.venv/.../popoto/models/base.py:2707-2776`):**
- popoto's `rebuild_indexes()` itself executes a **delete-then-reconstruct** sequence: it deletes
  the class set, all sorted-field indexes, key-field index sets, geo indexes, and composite indexes
  via `scan_iter` + `DELETE`, *then* SCANs all instance hashes and re-runs `on_save()` hooks to
  rebuild. There is no shadow/atomic-swap inside popoto.
- Therefore `repair_indexes()` is destructive in **two layers**: (1) the model's own
  `$IndexF:AgentSession:*` delete loop, then (2) popoto's internal index-key deletes inside
  `rebuild_indexes()`. A reader can observe an empty index during *either* layer.
- Redis `RENAME` is atomic and `O(1)`; a shadow-key build + `RENAME` swap is the canonical pattern
  for zero-window index rebuilds. But it would have to wrap popoto-internal keys the model does not
  enumerate, making a clean shadow-swap of *all* index keys non-trivial. This pushes the evaluation
  toward "prune-only-stale-members" or "skip destructive clear when additive" rather than a full
  shadow-RENAME.

## Spike Results

Two spikes resolve the quantification questions the issue asks for. Both are code-read /
measurement spikes against a realistic local keyspace — no committed code.

### spike-1: Measure the clear→rebuild window and concurrent-lookup failure probability
- **Assumption**: "The non-atomic window is wide enough to produce real `Session not found`
  flakes for a concurrent poller on a realistic keyspace."
- **Method**: prototype (worktree-isolated)
- **Agent Type**: builder (in worktree)
- **Time cap**: 5 minutes agent time
- **Result**: _filled during Phase 1.5 dispatch_ — measure wall-clock duration of the `$IndexF`
  delete loop + `rebuild_indexes()` against N≈ current session count, then run a tight
  `query.filter(session_id=...)` poller in a second connection during the rebuild and record the
  empty-result rate.
- **Confidence**: _tbd_
- **Impact if false**: If the window is sub-millisecond and the failure probability is negligible,
  the remediation collapses to "add a bounded retry on the critical read paths" only (drop the
  atomic-rebuild work). If the window is wide, the rebuild-side fix is justified.

### spike-2: Confirm TTL/index desync as the dominant stale-member source
- **Assumption**: "30-day `Meta.ttl` hash expiry without coordinated `$IndexF` expiry is the
  dominant producer of stale index members (vs. delete paths that already maintain the index)."
- **Method**: code-read + measurement
- **Agent Type**: Explore (code-read) + a measurement pass
- **Time cap**: 5 minutes agent time
- **Result**: _filled during Phase 1.5 dispatch_ — audit every `session.delete()` / status-transition
  path to confirm it maintains `$IndexF`; sample current `$IndexF` members and classify each phantom
  as (a) TTL-expired hash vs (b) un-maintained delete. Report the ratio.
- **Confidence**: _tbd_
- **Impact if false**: If delete paths are the dominant leak source, the fix shifts from
  "coordinate index expiry with TTL" to "patch the leaking delete path"; the destructive rebuild
  may then be reducible to a rare safety-net rather than an hourly necessity.

## Data Flow

The failure is a read/write race across two independent processes sharing one Redis DB:

1. **Writer (cleanup reflection or worker startup)**: `repair_indexes()` →
   loop `DELETE $IndexF:AgentSession:*` (`models/agent_session.py:2069-2074`) →
   `rebuild_indexes()` → (popoto) `DELETE` all secondary index keys → SCAN hashes →
   `on_save()` rebuild → repopulated `$IndexF`.
2. **Reader (CLI / worker / steering)**: `query.filter(session_id=<id>)`
   (`tools/valor_session.py:613`, `tools/sdlc_stage_query.py:62`, `tools/send_message.py:87`,
   `agent/session_executor.py:567/1078/1276`, `worker/idle_sweeper.py:124`,
   `agent/health_check.py:57`, …) reads `$IndexF:AgentSession:session_id:<id>` →
   **empty during the writer's window** → `Session not found`.
3. **Output**: CLI prints `Session not found`; worker recovery / steering may skip a live session.

The race is at the **index-key layer in Redis**, not in any single process's memory — so the fix
must live at the rebuild site (close the window) and/or be defended at each read site (bounded retry).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1459 read-side phantom filter (`_filter_hydrated_sessions`) | Drops members whose hash is gone, on read | Helps the dead-hash case; does NOT help the empty-window case — when the index key is deleted there is nothing to filter |
| #1361 unconditional `repair_indexes()` | Removed the gate so drift is always flushed | Increased correctness but also made the destructive window hit **hourly**, widening exposure to the race |
| #1006 chose `repair_indexes()` over `rebuild_indexes()` at pickup | Stronger `$IndexF` cleanup at startup | Same non-atomic clear→rebuild shape; window present at every worker startup |

**Root cause pattern:** Every prior fix improved index *correctness* by making the destructive
rebuild run more often or more thoroughly, without addressing the *atomicity* of the rebuild
itself. The window is a structural property of clear-then-rebuild, untouched by any prior fix.

## Architectural Impact

- **New dependencies**: None. Uses existing Popoto / Redis primitives.
- **Interface changes**: `repair_indexes()` signature stays `() -> tuple[int, int]`. A new bounded
  retry helper may be added for the read paths (internal).
- **Coupling**: A full shadow-RENAME would couple the model to popoto-internal index-key names
  (which popoto's own `rebuild_indexes()` enumerates but does not expose) — **increases coupling**,
  which is a reason to prefer a less invasive option. Prune-only-stale-members keeps coupling flat.
- **Data ownership**: Unchanged — `AgentSession` still owns its index maintenance.
- **Reversibility**: High. The rebuild-side change is a single method; the read-side retry is a thin
  wrapper. Both revert cleanly.

## Appetite

**Size:** Medium

**Team:** Solo dev, debugging-specialist (spike measurement), code-reviewer

**Interactions:**
- PM check-ins: 1-2 (after spikes, to confirm which remediation the data justifies)
- Review rounds: 1 (this path has a history of collateral damage — #1069 — so one careful review)

This is an investigation-first item. The build scope is deliberately small and gated on spike
findings; most of the appetite is measurement and the careful, conservative remediation.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.models.query import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Measure window / read index keys |
| Popoto installed | `python -c "import popoto; print(popoto.__version__)"` | Rebuild internals available |

Run all checks: `python scripts/check_prerequisites.py docs/plans/repair_indexes_atomic_rebuild.md`

## Solution

### Key Elements

- **Quantification harness**: A measurement that times the `repair_indexes()` clear→rebuild and
  records the empty-window failure rate for a concurrent `session_id` poller. This is the primary
  deliverable of the investigation; the remediation is gated on its output.
- **Stale-member audit**: Classify current `$IndexF:AgentSession:*` phantoms by source
  (TTL-expired hash vs un-maintained delete path) to confirm hypothesis #2.
- **Window-closing remediation (gated)**: The lowest-risk option the data supports —
  (a) **prune-only-stale-members**: iterate members and `SREM` only those whose hash is gone,
  never `DELETE` the whole index key, then call `rebuild_indexes()` only if drift remains; or
  (b) **skip-destructive-clear-when-additive**: if no stale members are found, skip the clear
  entirely (rebuild is purely additive). Full shadow-key + `RENAME` is evaluated and most likely
  **rejected** because popoto's internal `rebuild_indexes()` deletes index keys this model does
  not enumerate (see Architectural Impact).
- **Read-path bounded retry (gated)**: A short, bounded retry (e.g. 2-3 attempts, low-ms backoff)
  around the critical `query.filter(session_id=...)` reads in the CLI status path and worker
  recovery — defense-in-depth for any residual window.

### Flow

`agent-session-cleanup tick (or worker startup)` → `repair_indexes()` →
**[new] prune stale members in place (SREM), skip whole-key DELETE** →
`rebuild_indexes()` only if drift remains → indexes never observably empty →
concurrent `valor-session status --id` → finds the session → no `Session not found`.

### Technical Approach

- **Investigation first.** spike-1 and spike-2 run before any remediation code. Their numbers
  decide which remediation (if any) ships. If the window is negligible, remediation collapses to
  the read-path retry alone.
- **Prefer prune-only-stale-members** (`SREM` per dead member) over a full `DELETE` of each
  `$IndexF` key. This keeps every live member continuously present, so a concurrent reader never
  sees an empty index. It also matches the existing read-side phantom semantics from #1459.
- **Guard against the popoto-internal window.** Because popoto's `rebuild_indexes()` is itself
  destructive, call it **only when drift remains after the prune pass** — on a clean DB the prune
  pass leaves nothing to rebuild and the destructive popoto path is never entered. This is the
  "skip destructive clear when additive" option folded into the prune approach.
- **Bounded retry on critical reads** is additive and independent — it can ship even if the
  rebuild-side change is deferred. Apply it to the `_lookup_session`-style helper
  (`tools/valor_session.py:613`) and worker recovery, not to every read site (avoid latency creep
  on hot paths).
- **Conservatism per #1069 history.** No behavior change to phantom counting or `rebuild_indexes()`
  semantics beyond gating the destructive call.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit the `query.filter(session_id=...)` read sites for swallowed exceptions; the lookup
  helper at `tools/valor_session.py:613` returns `get_by_id` fallback — assert the retry path emits
  an observable signal (logger.debug/warning) when it retries, not silent.
- [ ] If no new exception handlers are introduced, state "No new exception handlers in scope" in the
  build PR.

### Empty/Invalid Input Handling
- [ ] Test `repair_indexes()` on an empty keyspace (no sessions) — must not error and must not call
  the destructive popoto path.
- [ ] Test `query.filter(session_id="")` and a non-existent id during a concurrent rebuild — must
  return empty cleanly (not raise), and the retry helper must not loop indefinitely.

### Error State Rendering
- [ ] `valor-session status --id <id>` during a rebuild: with the fix, must print the session
  status; without (regression test against current behavior) reproduces `Session not found`.

## Test Impact

- [ ] `tests/unit/` index/repair tests for `AgentSession` (search `repair_indexes`/`rebuild_indexes`
  in `tests/`) — UPDATE: assert live members are never `DELETE`d in the prune-only path; assert the
  destructive popoto rebuild is skipped on a clean keyspace.
- [ ] Add a NEW concurrency regression test: a poller running `query.filter(session_id=...)` against
  a known session while `repair_indexes()` runs in a second connection must never observe empty
  (post-fix). The same test, run against current `main` behavior, documents the flake.
- [ ] `tests/` for `tools/valor_session.py` lookup helper — UPDATE: assert bounded retry behavior
  (retries on empty, returns on found, terminates after the cap).

No existing tests are deleted — the change is additive and conservative; existing `repair_indexes`
phantom-count assertions stay valid as long as the prune pass preserves the `(stale_count, rebuilt_count)`
return contract.

## Rabbit Holes

- **Full shadow-key + atomic `RENAME` of all index keys.** Tempting and "clean," but popoto's
  internal `rebuild_indexes()` deletes index keys this model does not enumerate, so a complete
  shadow-swap would require reaching into popoto internals or forking the rebuild. Out of proportion
  to the win. Evaluate and reject in writing; do not build it.
- **Coordinating `$IndexF` TTL with `Meta.ttl` hash expiry.** Real but a much larger change to the
  Popoto layer; the prune-on-rebuild approach already neutralizes the symptom of TTL desync. Keep
  TTL-expiry coordination as a documented finding, not a build target in this slug.
- **Adding retry to every `query.filter(session_id=...)` call site.** Latency creep on hot worker
  paths. Limit retry to the operator CLI status path and worker recovery.
- **Rewriting the cleanup reflection.** The reflection wiring (`agent/session_health.py:2626`) is
  correct; only the rebuild atomicity is in scope.

## Risks

### Risk 1: Collateral damage to the cleanup/rebuild path (per #1069 history)
**Impact:** A subtle change to phantom handling could destroy valid sessions or leave genuine drift
unflushed (re-introducing the staleness #1361 fixed).
**Mitigation:** Preserve the `(stale_count, rebuilt_count)` return contract exactly; keep the
destructive popoto rebuild reachable when real drift exists; one mandatory code-review round; a
regression test that drift IS still flushed when present.

### Risk 2: Prune-only path is slower on a large keyspace (per-member `SREM` vs one `DELETE`)
**Impact:** The hourly cleanup tick takes longer.
**Mitigation:** Pipeline the `SREM`s; spike-1 measures the realistic keyspace size so the cost is
known before shipping. Cleanup runs hourly off the hot path, so modest extra latency is acceptable.

### Risk 3: Investigation concludes no remediation is justified
**Impact:** Build scope evaporates; only docs + read-path retry ship.
**Mitigation:** This is an acceptable outcome — the issue is explicitly investigation-first
("do not fix yet"). The plan is structured so the remediation is gated, not assumed.

## Race Conditions

### Race 1: Index clear→rebuild vs concurrent session_id lookup
**Location:** `models/agent_session.py:2069-2076` (writer) vs `tools/valor_session.py:613` and the
other `query.filter(session_id=...)` read sites (readers).
**Trigger:** A reader calls `query.filter(session_id=<id>)` after the writer has `DELETE`d the
`$IndexF` key but before `rebuild_indexes()` repopulates it (or during popoto's internal index
deletes).
**Data prerequisite:** The `$IndexF:AgentSession:session_id:<id>` set must contain the member for
the lookup to succeed; the writer transiently empties it.
**State prerequisite:** Cleanup reflection or worker startup is mid-rebuild concurrently with a poll.
**Mitigation:** Primary — prune-only-stale-members so live members are never removed (no empty
window). Defense-in-depth — bounded retry on the critical read paths. Both verified by the new
concurrency regression test.

## No-Gos (Out of Scope)

- `[DESTRUCTIVE]` Reworking Popoto's internal `rebuild_indexes()` to be shadow-key/atomic — this
  rewrites a shared library method that deletes index keys for every model, an irreversible-by-review
  change where review-before-execute is the safety mechanism. The prune-only approach in this plan
  neutralizes the symptom without touching popoto internals.

Everything else is in scope: quantification (spike-1), stale-member source audit (spike-2), the gated
prune-only window-closing remediation, and the read-path bounded retry are all built in this plan. The
`$IndexF`/`Meta.ttl` TTL-coordination question is handled as a documented finding from spike-2 (see
Rabbit Holes), not deferred — if spike-2 shows it is the decisive factor, that conclusion is recorded
in the feature doc within this plan's scope.

## Update System

No update system changes required — this feature is purely internal (a model method and read-path
hardening). No new dependencies, config files, or migration steps; the change deploys with the normal
code pull + worker restart that `/update` already performs.

## Agent Integration

No agent integration required — this is a bridge/worker-internal correctness fix. The agent already
reaches `valor-session status` via the existing `python -m tools.valor_session` CLI; no new CLI entry
point or MCP server is needed. The fix makes an existing CLI path more reliable; integration tests are
covered by the concurrency regression test in Test Impact rather than a new agent-facing surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` (or the nearest index-maintenance doc) with the
  atomic-rebuild behavior and the prune-only-stale-members rationale.
- [ ] Record the spike-1 / spike-2 measurements (window duration, failure rate, stale-member source
  ratio) in the plan's Spike Results and, if remediation ships, in the feature doc as the evidence
  basis.

### Inline Documentation
- [ ] Update the `repair_indexes()` docstring (`models/agent_session.py:2051`) to describe the
  prune-only behavior and why the destructive popoto rebuild is gated on residual drift.
- [ ] Docstring on the new bounded-retry helper explaining the window it defends against.

If the investigation concludes no remediation is justified, document that conclusion (and the
supporting measurements) in the feature doc instead — the negative result is itself the deliverable.

## Success Criteria

- [ ] spike-1 reports the clear→rebuild window duration and the concurrent-lookup empty rate on a
  realistic keyspace (the quantification the issue asks for).
- [ ] spike-2 reports the stale-`$IndexF`-member source ratio (TTL-expiry vs un-maintained delete),
  confirming or refuting hypothesis #2.
- [ ] An audit of the critical `query.filter(session_id=...)` read paths (CLI status, worker recovery,
  steering delivery) documents which lack a bounded retry.
- [ ] IF remediation is justified by the data: a concurrency regression test shows a concurrent
  `session_id` poller never observes empty during `repair_indexes()` (post-fix), and reproduces the
  flake against pre-fix behavior.
- [ ] The `(stale_count, rebuilt_count)` return contract of `repair_indexes()` is preserved, and a
  test confirms genuine drift is still flushed when present (#1361 / #1069 guard).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds
directly — they deploy team members and coordinate.

### Team Members

- **Spike Runner (measurement)**
  - Name: window-measurer
  - Role: Run spike-1 and spike-2, return quantified findings (window duration, failure rate, stale-member ratio)
  - Agent Type: debugging-specialist
  - Resume: true

- **Builder (index path)**
  - Name: index-builder
  - Role: Implement the gated prune-only-stale-members remediation and read-path bounded retry, per spike findings
  - Agent Type: builder
  - Resume: true

- **Validator (index path)**
  - Name: index-validator
  - Role: Verify the concurrency regression test, the preserved return contract, and the drift-still-flushed guard
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: index-documentarian
  - Role: Update session-lifecycle docs, docstrings, and record measurements
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(See template tiers — debugging-specialist, builder, validator, documentarian used here.)

## Step by Step Tasks

### 1. spike-1: Measure clear→rebuild window and concurrent-lookup failure rate
- **Task ID**: spike-window
- **Depends On**: none
- **Validates**: produces a measurement report (no test files)
- **Assigned To**: window-measurer
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Time `repair_indexes()` clear→rebuild against the current local keyspace
- Run a concurrent `query.filter(session_id=...)` poller in a second connection during the rebuild; record empty-result rate
- Report window duration and failure probability

### 2. spike-2: Classify stale-member source (TTL vs delete-path)
- **Task ID**: spike-stale-source
- **Depends On**: none
- **Validates**: produces a classification report
- **Assigned To**: window-measurer
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Audit every `session.delete()` / status-transition path for `$IndexF` maintenance
- Sample current `$IndexF:AgentSession:*` phantoms; classify each as TTL-expired vs un-maintained delete
- Report the ratio

### 3. Audit read paths for missing retry
- **Task ID**: audit-read-paths
- **Depends On**: none
- **Assigned To**: window-measurer
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Enumerate `query.filter(session_id=...)` sites (CLI status, worker recovery, steering, stage-query)
- Note which lack a bounded retry and which are hot paths (retry inadvisable)

### 4. Implement gated remediation (prune-only + read-path retry)
- **Task ID**: build-remediation
- **Depends On**: spike-window, spike-stale-source, audit-read-paths
- **Validates**: tests/unit + new concurrency regression test
- **Informed By**: spike-window (window size decides whether rebuild-side fix ships), spike-stale-source (source ratio decides TTL follow-up)
- **Assigned To**: index-builder
- **Agent Type**: builder
- **Parallel**: false
- If window is non-negligible: change `repair_indexes()` to prune stale members in place (pipelined `SREM`) and call popoto `rebuild_indexes()` only when residual drift remains
- Add bounded retry to the `tools/valor_session.py:613` lookup helper and worker recovery only
- Preserve the `(stale_count, rebuilt_count)` return contract

### 5. Validate remediation
- **Task ID**: validate-remediation
- **Depends On**: build-remediation
- **Assigned To**: index-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the concurrency regression test (no empty observed post-fix; flake reproduced pre-fix)
- Confirm genuine drift is still flushed when present
- Confirm return contract preserved and lint/format clean

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-remediation
- **Assigned To**: index-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` index-maintenance section
- Update `repair_indexes()` docstring and the retry-helper docstring
- Record spike measurements as the evidence basis (or the negative-result conclusion)

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: index-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table
- Confirm all success criteria met (including documentation and recorded measurements)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| repair_indexes contract | `pytest tests/ -k repair_indexes -q` | exit code 0 |
| Concurrency regression test exists | `grep -rln "repair_indexes" tests/ \| xargs grep -l "session_id" \| head -1` | output contains a path |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Remediation gating threshold.** What concurrent-lookup failure rate (from spike-1) justifies
   shipping the rebuild-side fix vs. shipping only the read-path retry? (Suggested default: any
   non-zero reproducible empty-window rate justifies the prune-only change, since it is low-risk.)
2. **Retry scope.** Is the bounded retry acceptable on the operator CLI status path only, or should
   worker recovery and steering delivery also get it? (Hot-path latency tradeoff.)
3. **TTL follow-up.** If spike-2 shows TTL desync dominates, should the `$IndexF`/`Meta.ttl`
   coordination be filed as a separate issue now, or held until the prune approach is proven
   insufficient in production?
