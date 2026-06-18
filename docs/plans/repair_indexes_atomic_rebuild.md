---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-18
tracking: https://github.com/tomcounsell/ai/issues/1720
last_comment_id:
revision_applied: true
---

# repair_indexes() Non-Atomic Rebuild — Investigation & Atomic Remediation

## Problem

`AgentSession.repair_indexes()` (`models/agent_session.py:2051-2077`) clears every
`$IndexF:AgentSession:*` index key and only **afterward** calls popoto's `rebuild_indexes()`
to repopulate them. **But that `$IndexF` clear is NOT the layer where the `session_id`
lookup fails.** `session_id` is a plain `Field()` (`models/agent_session.py:138`), so
`indexed == False` — there is **no `$IndexF:AgentSession:session_id:*` key at all**
(verified live: only `status` is an `IndexedField`). The original plan blamed a key that
does not exist for `session_id`.

**The failing layer is the class set.** A `query.filter(session_id=...)` on a non-indexed
field cannot use a secondary index — popoto resolves it by reading the **class set**
(`smembers($Class:AgentSession)`, `popoto/models/query.py:1341, 1758, 1790`) and filtering
in memory. The class set is emptied **inside popoto's own `rebuild_indexes()`**:
`POPOTO_REDIS_DB.delete(cls._meta.db_class_set_key.redis_key)` at
`popoto/models/base.py:2745`, after which members are re-`sadd`ed incrementally in
`batch_size=1000` pipeline batches (`base.py:2785-2813`). During that delete→re-add window
a concurrent `query.filter(session_id=...)` reads an empty or partial class set and returns
no result. The model's `$IndexF` delete loop (`agent_session.py:2069-2074`) never touches
this path.

This is exactly what `valor-session status --id <id>` does (`tools/valor_session.py:613`),
and the SDLC stage-query path (`tools/sdlc_stage_query.py:62`), and what worker recovery and
steering delivery do. The original symptom (`yudame/cuttlefish#512` / this repo's #496 run):
a freshly-created `AgentSession` was unretrievable shortly after creation —
`valor-session status --id <id>` → `Session not found` — breaking stage-by-stage dispatch.

`repair_indexes()` runs **hourly** (the `agent-session-cleanup` reflection,
`agent/session_health.py:2626`) and at **worker startup** (`agent/session_pickup.py:411`).
Each tick calls popoto's `rebuild_indexes()`, which re-empties the class set. So any reader
polling a recently-created session during the rebuild window can hit a transient
`Session not found`.

**Current behavior:** A concurrent reader during popoto's class-set delete→re-add window
gets an empty result from `query.filter(session_id=...)` and reports `Session not found` for
a session that exists and is valid. Self-healing (next read after rebuild succeeds), but a
real flake.

**Desired outcome:** First, *quantify* the class-set-empty window and the lookup-failure
probability and *confirm* the dominant source of stale members (the investigation the issue
asks for). The remediation, however, is **not** gated on those numbers: the corrected causal
model makes a **bounded read-path retry the primary, ungated fix** — it directly defends the
exact layer that fails (the class-set scan) regardless of window size, and covers both reader
sites (`tools/valor_session.py:613` AND `tools/sdlc_stage_query.py:62`). The spikes
re-target to *validate the class-set mechanism* and *size the retry cap* against batched
rebuild duration, not to decide whether the fix ships.

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

**Notes:** Line drift only on the model side; corrected file:line is `models/agent_session.py:2051-2077`.
The decisive finding surfaced during freshness verification (see Research) is a **re-anchoring
of the root cause**, not just drift:

- The original plan's docstring claim — that popoto's `rebuild_indexes()` "clears KeyField and
  SortedField indexes but not IndexedField (`$IndexF:`) indexes" — is **partially reconciled**:
  popoto's `rebuild_indexes()` does NOT enumerate `$IndexF` keys (true), but it DOES
  `POPOTO_REDIS_DB.delete()` the **class set** (`base.py:2745`) and every KeyField/SortedField
  index, then re-adds members incrementally in `batch_size=1000` batches (`base.py:2785-2813`).
  **It clears the class set — that is the failing layer for `session_id` lookups.** (C4)
- `session_id` is a plain `Field()` (`agent_session.py:138`), `indexed == False`. There is **no**
  `$IndexF:AgentSession:session_id:*` key (verified live). A `query.filter(session_id=...)` reads
  the class set (`query.py:1341/1758/1790`), filtered in memory. So the model's own `$IndexF`
  delete loop is **irrelevant** to the observed flake; the popoto class-set delete is the cause.

This re-anchoring is what drives the revised remediation: the ungated read-path retry (which
defends the class-set scan) is primary; the `$IndexF`-prune approach the original plan proposed
is dropped because it targets a layer the failing query never reads.

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

**Key findings (from reading `.venv/.../popoto/models/base.py:2707-2826` and
`.venv/.../popoto/models/query.py:1320-1360, 1745-1793`):**

- **The class set is the layer that fails for `session_id`.** popoto's `rebuild_indexes()`
  deletes the class set first — `POPOTO_REDIS_DB.delete(cls._meta.db_class_set_key.redis_key)`
  (`base.py:2745`) — then SCANs all instance hashes and re-`sadd`s them to the class set in
  `batch_size=1000` pipeline batches that flush at batch boundaries (`base.py:2782-2826`). A
  concurrent reader between the delete and the final batch flush sees an empty or partial class
  set.
- **`session_id` is not indexed.** `session_id = Field()` (`agent_session.py:138`) has
  `indexed == False`. Verified live: the only `$IndexF:AgentSession:*` key present is for
  `status` (the lone `IndexedField`). There is no `$IndexF:AgentSession:session_id:*` key.
- **A non-indexed filter reads the class set.** `AgentSession.query.filter(session_id=...)`
  cannot use a secondary index; popoto resolves it by reading
  `smembers(db_class_set_key)` (`query.py:1341, 1758, 1790`) and filtering in memory. This is
  the read that returns empty during the rebuild — confirming the failure is at the class-set
  layer, NOT the model's `$IndexF` delete loop.
- **C4 — docstring-vs-Research contradiction reconciled.** The `repair_indexes()` docstring
  says popoto's rebuild "clears KeyField and SortedField indexes but not IndexedField indexes."
  The accurate statement: popoto's rebuild does not enumerate `$IndexF` keys, but it *does*
  delete the **class set** (and KeyField/SortedField indexes). The class-set delete is the
  decisive one for `session_id`. The docstring is corrected in the Documentation section to say
  so, removing the contradiction.
- **Why not a shadow-RENAME of the class set.** Redis `RENAME` is atomic and `O(1)`, so a
  shadow-build-then-RENAME of the class set is conceivable. But the class-set delete + rebuild
  lives *inside* popoto's `rebuild_indexes()`, which this model calls but does not control; a
  shadow-swap would require reaching into or forking popoto's rebuild — out of proportion and
  explicitly out of scope (see No-Gos). The read-path retry defends the same window without
  touching popoto internals, which is why it is the primary fix.

## Spike Results

Two spikes **validate the corrected class-set mechanism** and **size the retry cap**. They do
NOT gate whether the fix ships — the ungated read-path retry is primary regardless (see Solution).
Both are measurement spikes against a realistic local keyspace — no committed code.

### spike-1: Measure the class-set delete→re-add window and size the retry cap (C2, C3)
- **Assumption**: "popoto's `rebuild_indexes()` empties the class set (`base.py:2745`) and re-adds
  members in `batch_size=1000` batches, producing a measurable window during which a concurrent
  `query.filter(session_id=...)` observes an empty/partial class set."
- **Method**: prototype (worktree-isolated)
- **Agent Type**: builder (in worktree)
- **Time cap**: 5 minutes agent time
- **Result**: _filled during Phase 1.5 dispatch_ — drive `AgentSession.rebuild_indexes()` directly
  (NOT the `$IndexF` clear) against N≈ current session count, instrumenting the wall-clock interval
  from the class-set `DELETE` (`base.py:2745`) to the final batch `sadd` flush (`base.py:2785-2826`).
  Run a tight `query.filter(session_id=<known-live-id>)` poller in a second Redis connection during
  the rebuild and record the empty-result rate and the **p99 class-set-empty duration**. The retry
  backoff total (attempts × backoff) MUST exceed that p99 — report the measured p99 so the cap is
  sized to data, not guessed.
- **Confidence**: _tbd_
- **Impact if false**: If the class set is never observably empty (e.g. popoto re-adds within a
  single atomic pipeline on this version), the retry cap can be minimal — but the retry still ships
  ungated as cheap defense-in-depth. The window size sets the retry cap, not whether the retry exists.

### spike-2: Confirm TTL/index desync as the dominant stale-member source (informational)
- **Assumption**: "30-day `Meta.ttl` hash expiry without coordinated index expiry is the dominant
  producer of stale class-set / `$IndexF` members (vs. delete paths that already maintain them)."
- **Method**: code-read + measurement
- **Agent Type**: Explore (code-read) + a measurement pass
- **Time cap**: 5 minutes agent time
- **Result**: _filled during Phase 1.5 dispatch_ — audit every `session.delete()` / status-transition
  path to confirm it maintains the class set and `$IndexF`; sample current members and classify each
  phantom as (a) TTL-expired hash vs (b) un-maintained delete. Report the ratio.
- **Confidence**: _tbd_
- **Impact if false**: This spike is now **informational only** — its conclusion is documented in the
  feature doc (Documentation section), not built. The read-path retry neutralizes the
  `Session not found` symptom irrespective of stale-member source, so this spike does not change this
  plan's remediation.

## Data Flow

The failure is a read/write race across two independent processes sharing one Redis DB. The
contended resource is the **class set** (`$Class:AgentSession`), not any `$IndexF` key:

1. **Writer (cleanup reflection or worker startup)**: `repair_indexes()` →
   `$IndexF` delete loop (`models/agent_session.py:2069-2074`, *irrelevant to `session_id`*) →
   popoto `rebuild_indexes()` → **`DELETE $Class:AgentSession`** (`base.py:2745`) → SCAN instance
   hashes → re-`sadd` members in `batch_size=1000` pipeline batches (`base.py:2785-2826`) →
   repopulated class set.
2. **Reader (CLI / stage-query / worker / steering)**: `query.filter(session_id=<id>)`
   (`tools/valor_session.py:613`, `tools/sdlc_stage_query.py:62`, and indirectly worker recovery /
   steering) resolves a non-indexed field by reading **`smembers($Class:AgentSession)`**
   (`query.py:1341/1758/1790`) and filtering in memory → **empty/partial during the writer's
   class-set delete→re-add window** → `Session not found`.
3. **Output**: CLI prints `Session not found`; SDLC stage-query returns `None`; worker recovery /
   steering may skip a live session.

The race is at the **class-set layer in Redis**, inside popoto's `rebuild_indexes()`. Because that
delete lives in a shared library this model does not control, the fix lives at the **read sites**
(bounded retry on the class-set scan), not at the model's `$IndexF` loop.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1459 read-side phantom filter (`_filter_hydrated_sessions`) | Drops members whose hash is gone, on read | Helps the dead-hash case; does NOT help the empty-class-set case — when the class set is deleted there is nothing to filter, and the reader scans an empty set |
| #1361 unconditional `repair_indexes()` | Removed the gate so the rebuild runs every cleanup tick | Increased correctness but also made the popoto class-set delete (`base.py:2745`) hit **hourly**, widening exposure to the race. Critically: because #1361 made the destructive rebuild the common case to flush drift, any "skip rebuild unless drift remains" gate cannot help — drift-bearing ticks are exactly when the rebuild runs, and each re-empties the class set |
| #1006 chose `repair_indexes()` over `rebuild_indexes()` at pickup | Stronger cleanup at startup | Still calls popoto `rebuild_indexes()` → same class-set delete; window present at every worker startup |

**Root cause pattern:** Every prior fix improved index *correctness* by making the rebuild run
more often or more thoroughly. None addressed the *atomicity* of popoto's class-set
delete→re-add — and that delete is inside a shared library this model calls but does not own.
The window is a structural property of popoto's `rebuild_indexes()`, untouched by any prior fix
and not removable from the model side without forking popoto. Hence the read-path retry, not a
rebuild-side gate, is the correct fix.

## Architectural Impact

- **New dependencies**: None. Uses existing Popoto / Redis primitives.
- **Interface changes**: `repair_indexes()` signature is **unchanged** (`() -> tuple[int, int]`);
  this plan no longer touches its body except the docstring fix. The primary change is a new
  bounded-retry helper applied at the two reader sites (internal).
- **Coupling**: The read-path retry adds **zero coupling to popoto internals** — it wraps the
  existing `query.filter(session_id=...)` call. (A class-set shadow-RENAME would have to reach into
  popoto's `rebuild_indexes()` to swap the class set it deletes — increased coupling, rejected; see
  No-Gos.)
- **Data ownership**: Unchanged — popoto owns the class set; `AgentSession` owns its readers.
- **Reversibility**: High. The read-side retry is a thin wrapper at two call sites; reverts cleanly.

## Appetite

**Size:** Medium

**Team:** Solo dev, debugging-specialist (spike measurement), code-reviewer

**Interactions:**
- PM check-ins: 1 (after spike-1, to confirm the retry cap against the measured p99)
- Review rounds: 1 (this path has a history of collateral damage — #1069 — so one careful review)

This is an investigation-first item for *quantification*, but the corrected causal model makes the
ungated read-path retry the obvious primary fix. The build scope is deliberately small: a shared
retry helper at two read sites plus a docstring correction. Most of the appetite is measurement
(spike-1 sizing the cap) and the careful regression test.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.models.query import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Measure window / read index keys |
| Popoto installed | `python -c "import popoto; print(popoto.__version__)"` | Rebuild internals available |

Run all checks: `python scripts/check_prerequisites.py docs/plans/repair_indexes_atomic_rebuild.md`

## Solution

### Key Elements

- **Bounded read-path retry (PRIMARY, ungated)**: A short, bounded retry around the
  `query.filter(session_id=...)` read at **both** reader sites — `tools/valor_session.py:613`
  AND `tools/sdlc_stage_query.py:62`. This is the corrected primary fix: it defends the exact
  layer that fails (the class-set scan), is independent of popoto internals, and ships regardless
  of spike numbers (C1). The retry retries on *empty* result, returns immediately on found, and
  terminates after a data-sized cap.
- **Retry cap sized to data (C2)**: The number of attempts × backoff is set so the total retry
  window **exceeds the p99 class-set-empty interval** measured by spike-1 (the class-set
  delete→re-add spans `batch_size=1000` pipeline batches, so the window scales with session
  count). The cap is a measured value, not a guess.
- **Mechanism-validating spike (spike-1)**: Drives `AgentSession.rebuild_indexes()` directly with
  a concurrent `session_id` poller, measuring the class-set-empty window and empty-result rate
  (C3). This validates the corrected causal model and produces the p99 that sizes the cap.
- **Stale-member audit (spike-2, informational)**: Classifies current phantoms by source
  (TTL-expiry vs un-maintained delete). Its finding is recorded in the feature doc as a documented
  conclusion within this plan's scope — it is NOT a gate on the read-path retry.

**Dropped (was primary in the prior plan):** the `$IndexF` prune-only-`SREM` remediation and the
"call `rebuild_indexes()` only when drift remains" gate. Both target a layer the failing query
never reads (`session_id` is not in any `$IndexF` key), and the gate cannot help because #1361
made drift-bearing ticks the common case — each re-empties the class set anyway (see Why Previous
Fixes Failed).

### Flow

`agent-session-cleanup tick (or worker startup)` → popoto `rebuild_indexes()` →
`DELETE $Class:AgentSession` → re-`sadd` in batches (transient empty/partial class set) →
concurrent `query.filter(session_id=<id>)` reads empty → **[new] bounded retry waits one
backoff and re-reads** → class set repopulated → finds the session → no `Session not found`.

### Technical Approach

- **Read-path retry is the primary, ungated fix (C1).** Wrap the `query.filter(session_id=...)`
  lookup at `tools/valor_session.py:613` AND `tools/sdlc_stage_query.py:62` (both reader sites,
  not just `valor_session.py`) in a shared bounded-retry helper: re-read on empty, return on
  found, stop after the cap. The helper falls through to `get_by_id` (valor_session) / `None`
  (stage_query) exactly as today after the cap — no behavior change on a genuinely-absent session.
- **Size the cap against batched rebuild duration (C2).** spike-1 reports the p99 class-set-empty
  interval; the retry's total backoff (attempts × per-attempt backoff) must exceed that p99 so a
  live session is found within the cap. Use low-ms backoff (e.g. 3 attempts × ~50ms) as the
  starting point, adjusted to the measured p99.
- **Emit an observable signal on retry.** When the helper retries (saw empty, will re-read), log
  at `debug`/`warning` so the retry path is not silent — feeds the Failure Path Test Strategy.
- **Do NOT modify `repair_indexes()` behavior.** Its body is unchanged except the docstring (C4).
  The class-set delete is popoto's; the model does not own it and the plan does not fork popoto.
- **Conservatism per #1069 history.** No change to phantom counting, `rebuild_indexes()` semantics,
  or the `(stale_count, rebuilt_count)` contract — the fix is confined to the two read sites.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Both reader sites — `tools/valor_session.py:613` AND `tools/sdlc_stage_query.py:62` — wrap
  the `query.filter(session_id=...)` read in the shared bounded-retry helper. Assert the retry path
  emits an observable signal (logger.debug/warning) when it retries on empty, not silent. The
  `sdlc_stage_query` site already swallows exceptions into `None` (line 70-72) — confirm the retry
  sits inside the `try` and still returns `None` cleanly after the cap.
- [ ] If no new exception handlers are introduced, state "No new exception handlers in scope" in the
  build PR.

### Empty/Invalid Input Handling
- [ ] Test the retry helper with `query.filter(session_id="")` and a genuinely non-existent id —
  must exhaust the cap and return the absent-session fallback (`get_by_id`/`None`), not loop
  indefinitely, not raise.
- [ ] Test `repair_indexes()` on an empty keyspace (no sessions) — must not error (unchanged
  behavior; confirms the plan did not regress the untouched method).

### Error State Rendering
- [ ] `valor-session status --id <id>` AND the SDLC stage-query path during a class-set rebuild:
  with the retry, must return the live session; without the retry (regression test against current
  behavior) reproduces the empty observation / `Session not found`.

## Test Impact

- [ ] Add a NEW concurrency regression test (C3) that **drives `AgentSession.rebuild_indexes()`
  directly** (NOT the `$IndexF` clear loop — that layer does not affect `session_id`) while a
  concurrent poller runs `query.filter(session_id=<known-live-id>)` in a second Redis connection.
  Assert: post-fix (retry in place) the poller never returns empty for the live session; pre-fix
  (retry disabled) the poller DOES observe empty — proving the test exercises the real class-set
  mechanism, not a false all-clear.
- [ ] `tests/` for `tools/valor_session.py` lookup helper — UPDATE/ADD: assert bounded-retry
  behavior (retries on empty, returns on found, terminates after the cap, falls through to
  `get_by_id`).
- [ ] `tests/` for `tools/sdlc_stage_query.py::_find_session_by_id` — ADD: same bounded-retry
  assertions (retries on empty, returns the live session, returns `None` after the cap on a genuine
  miss). This site was missing from the prior plan (C1).
- [ ] `tests/unit/` existing `repair_indexes`/`rebuild_indexes` tests (search those names in
  `tests/`) — VERIFY UNCHANGED: this plan does not modify `repair_indexes()` behavior, so its
  `(stale_count, rebuilt_count)` contract and existing phantom-count assertions stay valid. If any
  test asserted the old "prune" framing from a prior draft, none exists yet — greenfield for the
  retry tests.

No existing tests are deleted — the change is additive (retry at two read sites) and conservative;
the untouched `repair_indexes()` contract keeps its assertions valid.

## Rabbit Holes

- **Shadow-key + atomic `RENAME` of the class set.** Tempting and "clean," but popoto's
  `rebuild_indexes()` owns the class-set `DELETE` (`base.py:2745`); a shadow-swap would require
  reaching into or forking popoto's rebuild. Out of proportion to the win — the read-path retry
  defends the same window with zero popoto coupling. Evaluate and reject in writing; do not build it.
- **The `$IndexF` prune-only-`SREM` remediation (from the prior draft).** Targets a layer the
  failing `session_id` query never reads — `session_id` is not an `IndexedField`. Do NOT resurrect
  it; it would not fix the flake.
- **Coordinating class-set / `$IndexF` TTL with `Meta.ttl` hash expiry.** Real but a much larger
  change to the Popoto layer; the read-path retry already neutralizes the `Session not found`
  symptom regardless of stale-member source. spike-2's TTL-desync finding is recorded as a documented
  conclusion in the feature doc (Documentation section) — TTL coordination itself is out of scope for
  this slug and not built here.
- **Adding retry to every `query.filter(session_id=...)` call site.** Latency creep on hot worker
  paths. Limit retry to the two operator/dispatch reader sites named in the Solution
  (`tools/valor_session.py:613`, `tools/sdlc_stage_query.py:62`).
- **Modifying `repair_indexes()` or the cleanup reflection.** The reflection wiring
  (`agent/session_health.py:2626`) and the `repair_indexes()` body are correct/unchanged; only the
  read-path retry and a docstring clarification are in scope.

## Risks

### Risk 1: Retry masks a genuinely-absent session (false latency on real misses)
**Impact:** A lookup for a truly non-existent session now pays the full retry cap before returning
the absent-session fallback, adding latency to the common "not found" case.
**Mitigation:** Keep the cap small (e.g. 3 attempts × ~50ms ≈ 150ms worst case) at operator CLI
rates this is imperceptible; size to spike-1's p99, not larger. Both reader sites are
operator/dispatch paths (handful per day), not hot loops.

### Risk 2: Retry cap undersized — window exceeds total backoff on a large keyspace
**Impact:** On a very large keyspace the class-set re-add spans more `batch_size=1000` batches than
the retry covers, so a live session still reads empty after the cap.
**Mitigation:** spike-1 measures the p99 class-set-empty interval on a realistic keyspace; the cap
is set to exceed it (C2). The regression test asserts the live session is found within the cap.

### Risk 3: Spikes are not gates — fix ships regardless
**Impact:** None — this is the intended design. The read-path retry is ungated; spike-1 only sizes
the cap and validates the mechanism, spike-2 only produces a documented finding.
**Mitigation:** N/A — documented so a reviewer does not expect a "no remediation" branch. The issue
is investigation-first for *quantification*, but the corrected causal model makes the cheap,
low-risk retry obviously correct to ship.

## Race Conditions

### Race 1: Class-set delete→re-add vs concurrent session_id lookup
**Location:** popoto `rebuild_indexes()` class-set `DELETE` (`base.py:2745`) + batched re-`sadd`
(`base.py:2785-2826`), reached via `repair_indexes()` (writer) vs `tools/valor_session.py:613` and
`tools/sdlc_stage_query.py:62` (readers).
**Trigger:** A reader calls `query.filter(session_id=<id>)` — which reads `smembers($Class:...)`
(`query.py:1341/1758/1790`) — after popoto has `DELETE`d the class set but before the batched
re-`sadd` completes.
**Data prerequisite:** The class set `$Class:AgentSession` must contain the session's instance key
for the in-memory filter to find it; the writer transiently empties it.
**State prerequisite:** Cleanup reflection or worker startup is mid-`rebuild_indexes()` concurrently
with a poll.
**Mitigation:** Primary and ungated — bounded retry on the two reader sites re-reads the class set
after a backoff sized to spike-1's p99, so a live session is found within the cap. The class-set
delete itself is popoto-owned and not modified. Verified by the C3 concurrency regression test that
drives `rebuild_indexes()` directly.

## No-Gos (Out of Scope)

- `[DESTRUCTIVE]` Reworking Popoto's internal `rebuild_indexes()` to be shadow-key/atomic (e.g.
  shadow-build the class set + `RENAME`) — this rewrites a shared library method that deletes the
  class set for every model, an irreversible-by-review change. The read-path retry in this plan
  neutralizes the symptom without touching popoto internals.
- Resurrecting the `$IndexF` prune-only-`SREM` remediation from the prior draft — it targets a layer
  the `session_id` query never reads and would not fix the flake.

In scope: spike-1 (validate the class-set mechanism + size the retry cap), spike-2 (informational
stale-member audit, conclusion documented in the feature doc), the **ungated bounded read-path
retry at both reader sites** (`tools/valor_session.py:613` and `tools/sdlc_stage_query.py:62`), the
C3 concurrency regression test, and the docstring/feature-doc corrections. `Meta.ttl` TTL
coordination itself is out of scope for this slug — spike-2's conclusion is recorded as a documented
finding, not built here.

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
  corrected causal model: `session_id` is non-indexed, `query.filter(session_id=...)` reads the
  class set, and popoto's `rebuild_indexes()` transiently empties that class set — defended by the
  read-path retry.
- [ ] Record the spike-1 (class-set-empty window p99, empty-result rate) and spike-2 (stale-member
  source ratio) measurements in the plan's Spike Results and in the feature doc as the evidence basis.

### Inline Documentation
- [ ] **Correct the `repair_indexes()` docstring** (`models/agent_session.py:2052-2058`) (C4): the
  current text says popoto's rebuild "clears KeyField and SortedField indexes but not IndexedField
  indexes." Replace with the accurate statement — popoto's `rebuild_indexes()` deletes the **class
  set** and KeyField/SortedField indexes (it does not enumerate `$IndexF`), and the class-set delete
  is the layer that transiently breaks `session_id` lookups.
- [ ] Docstring on the new bounded-retry helper explaining the class-set-empty window it defends
  against and the p99-sized cap.

## Success Criteria

- [ ] spike-1 reports the **class-set delete→re-add window p99** and the concurrent-lookup empty
  rate on a realistic keyspace, driving `rebuild_indexes()` directly (validates the class-set
  mechanism, sizes the retry cap).
- [ ] spike-2 reports the stale-member source ratio (TTL-expiry vs un-maintained delete) —
  informational, recorded as a documented finding in the feature doc.
- [ ] The bounded read-path retry is applied at **both** reader sites — `tools/valor_session.py:613`
  AND `tools/sdlc_stage_query.py:62` — ungated.
- [ ] A C3 concurrency regression test drives `AgentSession.rebuild_indexes()` directly with a
  concurrent `query.filter(session_id=...)` poller: post-fix the poller never observes empty for a
  live session; pre-fix (retry disabled) it DOES observe empty (proving the test hits the class-set
  mechanism, not a false all-clear).
- [ ] The retry cap's total backoff exceeds spike-1's measured p99 class-set-empty interval (C2).
- [ ] `repair_indexes()` is unchanged except its docstring; its `(stale_count, rebuilt_count)`
  contract is verified intact.
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

- **Builder (read-path retry)**
  - Name: index-builder
  - Role: Implement the ungated bounded read-path retry at both reader sites (`tools/valor_session.py:613`, `tools/sdlc_stage_query.py:62`), sized to spike-1's p99
  - Agent Type: builder
  - Resume: true

- **Validator (read-path retry)**
  - Name: index-validator
  - Role: Verify the C3 concurrency regression test (drives `rebuild_indexes()` directly), the preserved `repair_indexes()` contract, and retry behavior at both sites
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

### 1. spike-1: Measure the class-set delete→re-add window and size the retry cap
- **Task ID**: spike-window
- **Depends On**: none
- **Validates**: produces a measurement report (no test files)
- **Assigned To**: window-measurer
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Drive `AgentSession.rebuild_indexes()` directly (NOT the `$IndexF` clear) against the current local keyspace; instrument the interval from the class-set `DELETE` (`base.py:2745`) to the final batch `sadd` flush
- Run a concurrent `query.filter(session_id=<known-live-id>)` poller in a second connection during the rebuild; record empty-result rate
- Report the **p99 class-set-empty duration** so the retry cap can be sized to exceed it

### 2. spike-2: Classify stale-member source (TTL vs delete-path) — informational
- **Task ID**: spike-stale-source
- **Depends On**: none
- **Validates**: produces a classification report
- **Assigned To**: window-measurer
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Audit every `session.delete()` / status-transition path for class-set + `$IndexF` maintenance
- Sample current phantoms; classify each as TTL-expired vs un-maintained delete
- Report the ratio; its conclusion is recorded in the feature doc, not a gate on the fix

### 3. Confirm the two reader sites
- **Task ID**: audit-read-paths
- **Depends On**: none
- **Assigned To**: window-measurer
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Confirm `tools/valor_session.py:613` and `tools/sdlc_stage_query.py:62` are the `query.filter(session_id=...)` sites needing the retry; confirm the fall-through (`get_by_id` / `None`) at each
- Confirm no hot-path site is mistakenly in scope (retry stays at these two only)

### 4. Implement ungated read-path retry at both reader sites
- **Task ID**: build-remediation
- **Depends On**: spike-window, audit-read-paths
- **Validates**: new C3 concurrency regression test + per-site retry tests
- **Informed By**: spike-window (p99 sizes the retry cap)
- **Assigned To**: index-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a shared bounded-retry helper: re-read on empty, return on found, stop after a cap sized to exceed spike-1's p99 (e.g. 3 × ~50ms); emit a debug/warning on retry
- Apply it at `tools/valor_session.py:613` AND `tools/sdlc_stage_query.py:62` (both sites — C1), preserving each site's existing absent-session fallback
- Do NOT modify `repair_indexes()` behavior; only correct its docstring (C4)

### 5. Validate remediation
- **Task ID**: validate-remediation
- **Depends On**: build-remediation
- **Assigned To**: index-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the C3 concurrency regression test that drives `rebuild_indexes()` directly (no empty observed post-fix; empty reproduced pre-fix with retry disabled)
- Confirm the retry fires at both reader sites and terminates at the cap on a genuine miss
- Confirm `repair_indexes()` contract preserved (unchanged) and lint/format clean

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
| repair_indexes contract intact | `pytest tests/ -k repair_indexes -q` | exit code 0 |
| C3 regression test exists | `grep -rln "rebuild_indexes" tests/ \| xargs grep -l "session_id" \| head -1` | output contains a path |
| Retry at both reader sites | `grep -l "retry" tools/valor_session.py tools/sdlc_stage_query.py` | both files listed |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Revision pass addressed the NEEDS REVISION verdict. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker (B1) | unanimous (3 FULL) | Plan blamed the wrong Redis key: `$IndexF:AgentSession:session_id` does not exist (`session_id` is a plain `Field()`, not indexed). | Problem / Freshness Check / Research / Data Flow re-anchored | `query.filter(session_id=...)` reads the **class set** (`smembers(db_class_set_key)`); the class set is emptied inside popoto's `rebuild_indexes()` (`base.py:2745`), not by the `$IndexF` loop. Verified live. |
| Blocker (B2) | unanimous (3 FULL) | Primary remediation (`$IndexF` prune-only `SREM` + "rebuild only if drift remains" gate) targets a layer that cannot fix the symptom; #1361 makes drift the common case so the gate re-empties the class set anyway. | Solution / Rabbit Holes / No-Gos | Prune-only approach dropped entirely; replaced by ungated read-path retry. |
| C1 | critique | The bounded read-path retry is the correct fix — promote to primary, ungated, covering BOTH reader sites. | Solution / Test Impact / Step by Step | Applies at `tools/valor_session.py:613` AND `tools/sdlc_stage_query.py:62`. |
| C2 | critique | Size the retry cap against batched rebuild duration (class-set re-add is `batch_size=1000`). | Solution / spike-1 / Risks | spike-1 reports the p99 class-set-empty interval; cap's total backoff must exceed it. |
| C3 | critique | spike-1 + regression test validated the wrong mechanism (`$IndexF` clear, false all-clear). | spike-1 / Test Impact / Step by Step | Regression test now drives `AgentSession.rebuild_indexes()` directly with a concurrent `session_id` poller. |
| C4 | critique | Reconcile docstring-vs-Research contradiction about what popoto's rebuild clears. | Research / Documentation | Landed: popoto's rebuild clears the **class set** (and KeyField/SortedField), not `$IndexF`; that is the failing layer. Docstring correction is a build task. |

---

## Open Questions

1. **Retry cap default.** spike-1 sizes the cap to its measured p99. Starting point is 3 attempts ×
   ~50ms (≈150ms worst case). Is that worst-case latency acceptable on a genuine miss at the two
   reader sites, or should the cap be tighter/looser pending the p99? (Default: 3 × 50ms, adjusted to
   spike-1's p99.)
2. **TTL coordination disposition.** If spike-2 shows TTL desync dominates stale members, is
   recording it as a documented finding sufficient for this slug (the read-path retry already fixes
   the symptom), or do you want the class-set/`Meta.ttl` coordination scoped as its own work item?
   (Default: document the finding here; the symptom is already fixed.)
