---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1873
last_comment_id: 4877996151
---

# Tool-budget + reclaim-bridge advisory cleanup (from #1872 review)

## Problem

PR #1872 (out-of-domain recovery + per-tool budget backstop, issue #1821) merged
REVIEW APPROVED with 0 blockers. Its reviewer and re-critique flagged four
advisory items — hardening opportunities, none load-bearing. Issue #1873 collects
them so they aren't lost. Three are small, isolated code cleanups; the fourth is
an observe-first tuning decision that needs live production data before any code
moves.

**Current behavior:**
- `monitoring/session_watchdog.py::_clear_reclaim_dedup` clears per-owner dedup
  markers with a blocking Redis `KEYS` scan (`redis_client.keys(pattern)`,
  line 924). `KEYS` is O(N) over the entire keyspace and blocks the Redis event
  loop — a production hazard at scale.
- `agent/session_health.py::_maybe_emit_bridge_contract_stale` (line 2941) makes
  its own second pass over `leases_snapshot`, re-reading each owner via
  `AgentSession.get_by_id(lease.owner_session_id)` (line 2971) within the same
  reap tick that the Phase-2 reap loop (line 2778) also reads the same owners —
  up to two redundant per-owner Redis round-trips per tick over identical data.
- `agent/tool_budget.py::record_budget_trip` derives its dedup key from
  `session_id = getattr(session,"session_id",None) or getattr(session,"agent_session_id",None)`
  (line 199). When both are absent the key serializes as
  `{project_key}:tool-budget:tripped_applied:None` — a single shared slot that
  collapses every id-less session together, so the first trip surfaces and every
  subsequent id-less trip is silently deduped away (counter never increments,
  `budget_tripped` flag never set).
- With `TOOL_BUDGET_AUTO_PAUSE` off (the shipped default), a denied headless/SDK
  session keeps metering one harness round-trip per denied tool call until
  max-turns — the deny blocks each call but never halts the session. Whether the
  wasted round-trips justify flipping the default or adding a consecutive-denial
  hard-stop cannot be answered without live denial-distribution data, which is
  not currently emitted per-denial (the `tripped` counter increments once per
  session, not once per denied call).

**Desired outcome:**
- Reclaim-dedup clearing uses a non-blocking `SCAN`-based iterate-and-delete.
- The terminal-owner determination over `leases_snapshot` happens once per reap
  tick and is shared, eliminating the redundant re-reads while preserving the
  deliberate #1868 divergence in how `None` (not-found) is treated.
- `record_budget_trip` never collapses distinct id-less sessions into one dedup
  slot — trips surface reliably even when a session has no resolvable id.
- The deny-but-don't-halt tradeoff is documented, and a raw per-denial counter is
  emitted so the production data needed for the item-4 decision starts
  accumulating now. The decision itself is tracked separately (#1886).

## Freshness Check

**Baseline commit:** `20c9e2a5a2a356b4ecb75075d9ee1be7f4ea66bd`
**Issue filed at:** 2026-07-02T22:19:23Z
**Disposition:** Unchanged

**File:line references re-verified (against baseline HEAD):**
- `monitoring/session_watchdog.py:924` — `_clear_reclaim_dedup` uses
  `redis_client.keys(pattern)` then `redis_client.delete(*keys)`. Still holds.
- `agent/session_health.py:2941`/`2971` — `_maybe_emit_bridge_contract_stale`
  re-reads `leases_snapshot` owners via `AgentSession.get_by_id`. Still holds.
  The enclosing `_reap_slot_leases` Phase-2 loop at `2778` fetches the same
  owners. Still holds.
- `agent/tool_budget.py:199`/`205` — `record_budget_trip` resolves `session_id`
  with an `or`-fallback and builds `...:tripped_applied:{session_id}`. Still
  holds; the `:None` collapse is reachable when both id attributes are absent.

**Cited sibling issues/PRs re-checked:**
- #1872 — merged 2026-07-02T22:16:57Z (the source of these advisory items). Its
  diff is the origin of all three code sites; unchanged since merge.
- #1821 — parent resilience issue; closed. No bearing on the cleanup scope.
- #1868 — the deliberate "None → unknown, do not reclaim" divergence in the
  request-driven drain / stale-check vs. the autonomous reaper's "None → terminal".
  This invariant MUST be preserved by the item-2 refactor.

**Commits on main since issue was filed (touching referenced files):**
- `agent/tool_budget.py` — none.
- `monitoring/session_watchdog.py` — none.
- `agent/session_health.py` — `d9cb76b1`, `46850300`, `6e846f0d` touched the
  file but `git log -L :_maybe_emit_bridge_contract_stale:` shows none touched
  the target function. Irrelevant to this scope.

**Active plans in `docs/plans/` overlapping this area:** none. (Several `granite-*`
plans exist but none touch tool_budget, session_watchdog reclaim-dedup, or the
bridge-contract-stale path.)

**Notes:** All three code sites are byte-for-byte the merged #1872 diff. Line
numbers above are current as of the baseline SHA.

## Prior Art

- **PR #1872**: "Resilience: out-of-domain recovery + per-tool budget backstop
  (wedge fixes #5/#6)" — merged 2026-07-02. Introduced all three code sites. This
  plan hardens that shipped code; it does not re-solve the feature.
- Closed-issue search (`tool budget reclaim SCAN dedup`) returned no prior
  attempts at these specific cleanups. This is first-touch hardening.

## Data Flow

Two independent code paths, no shared data flow between them:

**Item 1 (reclaim-dedup clear):** worker liveness tick → `_reap_slot_leases`
publishes lease snapshot → on a *healthy* watchdog tick with no terminal owners,
`_clear_reclaim_dedup(POPOTO_REDIS_DB, host)` wipes the per-owner dedup markers so
a future re-leak re-triggers a fresh reclaim-request. The only change is *how* the
markers are enumerated for deletion (KEYS → SCAN).

**Item 2 (terminal-owner determination):** `_reap_slot_leases` reads
`leases_snapshot = list(registry.leases())` once per tick → currently three
consumers each independently fetch owner status: `_drain_reclaim_requests` (reads
*request* owners popped from Redis, a distinct set — no overlap),
`_maybe_emit_bridge_contract_stale` (iterates `leases_snapshot`), and the Phase-2
reap loop (iterates `leases_snapshot`). The two lease-snapshot consumers do the
redundant work; the fix computes the owner→record map once and shares it.

**Item 3 (trip dedup):** PreToolUse hook (both `agent/hooks/pre_tool_use.py` and
`.claude/hooks/pre_tool_use.py`) → `evaluate_tool_budget(session)` → on deny,
`record_budget_trip(session, verdict)` → dedup gate → counter + flag + (auto-pause
extras). The change is in how the dedup key is formed / gated when no session id
is resolvable, plus a raw per-denial counter emitted before the gate.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified from the #1872 review)
- Review rounds: 1

Three isolated, low-risk cleanups plus one doc + one small counter. The care is
in preserving two shipped invariants (the #1868 None-divergence and the
fail-quiet posture of every touched function), not in coding volume.

## Prerequisites

No prerequisites — this work modifies existing internal code paths and has no new
external dependencies. Redis is already a hard dependency of every touched module.

## Solution

### Key Elements

- **SCAN-based dedup clear** (`_clear_reclaim_dedup`): iterate matching keys with
  `scan_iter` and delete in bounded batches, replacing the blocking `KEYS`.
- **Single-pass owner map** (`_reap_slot_leases` / `_maybe_emit_bridge_contract_stale`):
  fetch each `leases_snapshot` owner once, build an owner→record map, and thread
  it into both the stale-check and the Phase-2 reap loop, each applying its own
  `None` policy.
- **Id-safe trip dedup** (`record_budget_trip`): when no session id resolves,
  skip the shared-key dedup gate rather than write `...:None`; add a raw
  per-denial `denied_calls` counter incremented before the gate.
- **Deny-but-don't-halt documentation** + tracking of the data-gated decision in
  #1886.

### Flow

Watchdog healthy tick → `_clear_reclaim_dedup` → `scan_iter(match)` → batched
`delete` (no keyspace block).

Reap tick → build owner→record map once → stale-check reads map → Phase-2 loop
reads map → same result, half the Redis reads.

Tool deny → `record_budget_trip` → increment `denied_calls` → resolve id → if id
present: NX-dedup gate as today; if id absent: skip gate, surface every time.

### Technical Approach

- **Item 1** — In `_clear_reclaim_dedup`, replace
  `keys = list(redis_client.keys(pattern))` with an accumulate-and-delete over
  `redis_client.scan_iter(match=pattern, count=100)`, deleting in batches (e.g.
  every 500 keys) to bound the DEL argument list, then a final flush. Keep the
  whole body inside the existing `try/except … logger.debug(...)` — fail-quiet is
  preserved; orphaned markers still age out via TTL. These are plain watchdog
  marker keys (prefix `WORKER_SLOT_RECLAIM_DEDUP_KEY_PREFIX`), NOT Popoto-managed
  model keys, so raw `scan_iter`/`delete` is permitted (the existing `delete` on
  the same keys already passes `validate_no_raw_redis_delete.py`).

- **Item 2** — In `_reap_slot_leases`, before the drain call, build
  `owner_records: dict[str, AgentSession | None]` by fetching each
  `lease.owner_session_id` once. Distinguish three states so the deliberate
  #1868 divergence survives: key present with a record, key present with `None`
  (positively not-found), key ABSENT (lookup raised → unknown). Change
  `_maybe_emit_bridge_contract_stale(drained, leases_snapshot)` to
  `_maybe_emit_bridge_contract_stale(drained, owner_records)`; its
  `terminal_owner_present` check reads the map and keeps its "found record AND
  status terminal" policy (a `None` value or absent key is NOT terminal → skip,
  unchanged). The Phase-2 reap loop reads the same map and keeps its "record is
  `None` (not-found) OR status terminal → reclaim" policy; an ABSENT key (lookup
  error) still logs+continues without reclaiming, exactly as the current
  per-iteration `try/except` does. Net effect: one fetch per owner per tick, both
  policies byte-for-byte preserved. Fail-quiet: map construction is wrapped so a
  fetch error records "absent key" (unknown) rather than raising.

- **Item 3** — In `record_budget_trip`: increment
  `{project_key}:tool-budget:denied_calls` immediately (before the dedup gate,
  inside the existing outer `try`) so EVERY deny is counted — this is the raw
  distribution data #1886 needs. Then, when `session_id` is falsy, bypass the NX
  dedup gate entirely (surface the counter/flag/log on every id-less deny) rather
  than reading/writing the shared `...:None` key. When `session_id` is present,
  behavior is unchanged (NX gate on `...:tripped_applied:{session_id}`). The
  `budget_tripped` field write is naturally idempotent per session object, so
  ungated surfacing for id-less sessions is safe. All still inside the outer
  fail-quiet `try/except`.

- **Item 4** — No behavioral code change to the deny/pause logic. Document the
  deny-but-don't-halt tradeoff (why auto-pause ships off, the per-denied-call
  metering cost, and the decision criteria) in the tool-budget feature doc. The
  `denied_calls` counter from item 3 is the instrument that makes the future
  decision data-driven; the decision itself is tracked in #1886 and is out of
  scope here (no live production data yet).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_clear_reclaim_dedup` keeps its `except Exception … logger.debug(...)`
  wrapper — add/keep a test asserting a raising Redis client is swallowed (no
  raise out of the function).
- [ ] `_maybe_emit_bridge_contract_stale` and the new owner-map construction keep
  their fail-quiet wrappers — assert a fetch that raises records "absent/unknown"
  and does not raise into the reap pass, and does not reclaim on unknown.
- [ ] `record_budget_trip` keeps its outer `try/except` — the existing
  `test_surfacing_error_is_fail_quiet` must still pass; extend it to confirm the
  `denied_calls` increment being made before the gate does not change fail-quiet
  behavior.

### Empty/Invalid Input Handling
- [ ] `_clear_reclaim_dedup` with zero matching keys must issue no `delete` (empty
  batch) and not raise.
- [ ] `record_budget_trip` with a session whose `session_id` AND
  `agent_session_id` are both `None` must NOT write a `...:None` dedup key and
  must surface (counter + flag) on every call.
- [ ] `_maybe_emit_bridge_contract_stale` with an empty owner map → no stale
  emission, no raise.

### Error State Rendering
- [ ] No user-visible output surface changes. The observability surfaces
  (counters, `logger.warning` on stale/trip) are asserted via counter reads and
  caplog in the tests above.

## Test Impact

- [ ] `tests/integration/test_tool_budget_enforcement.py::test_surfacing_error_is_fail_quiet`
  — UPDATE: still fail-quiet, and assert the pre-gate `denied_calls` increment is
  attempted.
- [ ] `tests/integration/test_tool_budget_enforcement.py::test_auto_pause_transitions_and_queues_telegram_once`
  — UPDATE: the double `record_budget_trip` call still surfaces once for an
  id-bearing session (dedup path unchanged); assert `denied_calls == 2` (raw
  counter counts both denies) while `tripped == 1` (per-session dedup).
- [ ] `tests/integration/test_tool_budget_enforcement.py` — ADD
  `test_id_less_session_does_not_collapse_dedup`: two sessions with `session_id`
  and `agent_session_id` both `None` each trip; assert both surface (no shared
  `:None` collapse) and `denied_calls` counts both.
- [ ] `tests/integration/test_out_of_domain_reclaim.py` — UPDATE: this file
  already covers the reclaim/dedup/bridge-contract-stale paths. Add/adjust cases
  for (a) `_clear_reclaim_dedup` uses SCAN and deletes matching markers, (b) the
  single owner→record map yields the same reclaim + stale-emit decisions as the
  pre-refactor double-read, including the #1868 `None`-is-unknown-for-stale vs.
  `None`-is-terminal-for-reap divergence.

No other existing tests exercise these three functions (grep confirmed:
`record_budget_trip` only in `test_tool_budget_enforcement.py`;
`_clear_reclaim_dedup` / `bridge_contract_stale` only in
`test_out_of_domain_reclaim.py`).

## Rabbit Holes

- **Rewriting the reclaim-request / drain architecture.** Item 2 is a
  read-deduplication, not a redesign. Do NOT merge the request-owner drain (a
  different owner set) into the lease-snapshot map, and do NOT change when
  reclaims fire. Touch only the double-read over `leases_snapshot`.
- **"Fixing" the #1868 None-divergence.** The stale-check treating `None` as
  unknown while the reaper treats `None` as terminal LOOKS inconsistent but is
  deliberate and load-bearing. Preserve both policies exactly; the refactor only
  shares the fetch, never the policy.
- **Implementing item 4's behavior change now.** Flipping the auto-pause default
  or adding a consecutive-denial hard-stop without production data is exactly the
  speculative change the reviewer deferred. Emit the counter, write the doc, stop.
- **A general Popoto/Redis SCAN utility.** Item 1 is a two-line local swap; do not
  build a shared scan-delete helper for one call site.

## Risks

### Risk 1: Item-2 refactor silently changes reclaim or stale-emit behavior
**Impact:** A shared owner map that flattens the not-found vs. lookup-error vs.
found-record states would either over-reclaim (strip a live session's permit) or
stop emitting `bridge_contract_stale`.
**Mitigation:** The map preserves three distinct states; each consumer keeps its
existing `None`/absent policy. Test parity against the pre-refactor decisions in
`test_out_of_domain_reclaim.py`, explicitly covering a terminal record, a
not-found (`None`) owner, and a lookup-error owner.

### Risk 2: SCAN swap regresses under a large keyspace or empty match
**Impact:** An unbounded DEL arg list or a delete on an empty batch could error.
**Mitigation:** Batch deletes (bounded arg list); guard the final flush on a
non-empty batch; keep the fail-quiet wrapper. Test the zero-match case.

### Risk 3: Ungated id-less trip surfacing spams counters/logs
**Impact:** An id-less session that trips repeatedly would increment `tripped`
and log on every call (no dedup).
**Mitigation:** Acceptable and strictly better than the current silent-drop; the
`budget_tripped` flag write is idempotent, and id-less sessions do not occur in
the shipped call paths (both hook surfaces pass a persisted `AgentSession` with a
`session_id`). This is defensive hardening for a path that should not arise, so
the log/counter volume is bounded in practice.

## Race Conditions

No new race conditions. All three changes operate within existing single-threaded
tick/hook execution:
- Item 1 runs on the watchdog tick; SCAN + batched DELETE is not more racy than
  the current KEYS + DELETE (both non-atomic against concurrent marker writes,
  and both idempotent — a re-leak simply re-triggers).
- Item 2 reads a single `leases_snapshot` captured once per tick; sharing the
  fetch reduces, not increases, the read window.
- Item 3's `denied_calls` `INCR` is atomic; the NX dedup gate semantics are
  unchanged for id-bearing sessions.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1886] Changing the deny-but-don't-halt default (flipping
  `TOOL_BUDGET_AUTO_PAUSE` on) or adding a consecutive-denial hard-stop. This is
  genuinely blocked on live denial-distribution data, which item 3's
  `denied_calls` counter begins collecting. The decision is tracked in #1886 and
  must not be pre-empted here.

## Update System

No update system changes required. All three edits are internal to already-shipped
modules (`monitoring/session_watchdog.py`, `agent/session_health.py`,
`agent/tool_budget.py`); no new dependencies, config files, env vars, or Popoto
schema changes (the `denied_calls` counter is a plain Redis key, not a model
field). Nothing new to propagate via `/update`.

## Agent Integration

No agent integration required. These are worker/watchdog-internal code paths
(slot-lease reclaim and the PreToolUse budget hook); none is reachable as an agent
tool or MCP surface, and the bridge does not call them directly. The new
`denied_calls` counter surfaces via the existing analytics/dashboard counter-read
path, not a new tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/` tool-budget documentation (locate via
  `grep -rl "TOOL_BUDGET_AUTO_PAUSE\|per-tool budget" docs/features/`; if no
  dedicated page exists, add a section to the nearest owning doc, e.g. the
  session-health / resilience feature doc): document the deny-but-don't-halt
  behavior, why auto-pause ships off by default, the per-denied-call metering
  cost, the new `{project_key}:tool-budget:denied_calls` counter, and the
  decision criteria for the future default change (cross-reference #1886).
- [ ] Update `docs/features/slot-lease-ownership.md` (or the reclaim doc it points
  to) to note the SCAN-based dedup clear and the single-pass owner-map read in the
  reap tick.

### Inline Documentation
- [ ] Update the `_clear_reclaim_dedup`, `_maybe_emit_bridge_contract_stale`, and
  `record_budget_trip` docstrings to reflect the SCAN clear, the shared owner map
  (and the preserved #1868 None-divergence), and the id-less dedup guard +
  `denied_calls` counter respectively.

## Success Criteria

- [ ] `_clear_reclaim_dedup` contains no `.keys(` call and uses `scan_iter`
  (`grep -n "scan_iter" monitoring/session_watchdog.py` matches;
  `grep -n "\.keys(" monitoring/session_watchdog.py` in that function does not).
- [ ] `_maybe_emit_bridge_contract_stale` no longer calls
  `AgentSession.get_by_id` (it reads the passed owner map); the owner→record map
  is built once in `_reap_slot_leases`.
- [ ] `record_budget_trip` never forms a `tripped_applied:None` key; two id-less
  sessions both surface (new test passes); `denied_calls` increments on every
  deny.
- [ ] The #1868 divergence (stale-check: `None`→unknown/skip; reaper:
  `None`→terminal/reclaim) is preserved and covered by tests.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Single builder + validator; the three edits are independent files and can be
built in one pass, then verified together.

### Team Members

- **Builder (advisory-cleanup)**
  - Name: cleanup-builder
  - Role: Implement items 1-3 code changes + item-4 counter and docs
  - Agent Type: builder
  - Domain: async/Redis, Popoto data
  - Resume: true

- **Validator (advisory-cleanup)**
  - Name: cleanup-validator
  - Role: Verify SCAN swap, owner-map parity (incl. #1868 divergence), id-less
    dedup guard, and all success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Item 1 — SCAN-based reclaim-dedup clear
- **Task ID**: build-scan-clear
- **Depends On**: none
- **Validates**: tests/integration/test_out_of_domain_reclaim.py
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `redis_client.keys(pattern)` in `_clear_reclaim_dedup` with a
  `scan_iter(match=pattern, count=100)` accumulate + batched `delete`, preserving
  the fail-quiet `try/except` and TTL fallback.
- Domain (Redis): these are plain watchdog marker keys, not Popoto model keys —
  raw scan/delete is permitted; keep it fail-quiet.

### 2. Item 2 — single-pass owner→record map
- **Task ID**: build-owner-map
- **Depends On**: none
- **Validates**: tests/integration/test_out_of_domain_reclaim.py
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_reap_slot_leases`, build `owner_records` (3-state: record / not-found
  `None` / absent-key-on-error) once before the drain; pass it to
  `_maybe_emit_bridge_contract_stale` and reuse it in the Phase-2 reap loop.
- Preserve the #1868 divergence exactly: stale-check `None`/absent → not terminal
  → skip; reaper `None` → terminal → reclaim; absent (lookup error) → skip.

### 3. Item 3 + Item 4 counter — id-safe trip dedup and denied_calls
- **Task ID**: build-trip-dedup
- **Depends On**: none
- **Validates**: tests/integration/test_tool_budget_enforcement.py
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- In `record_budget_trip`: increment `{project_key}:tool-budget:denied_calls`
  before the dedup gate; when `session_id` is falsy, bypass the NX gate (surface
  every time) instead of writing `...:None`.
- Keep id-bearing behavior unchanged; keep the outer fail-quiet `try/except`.

### 4. Documentation (item 4 + inline)
- **Task ID**: document-cleanup
- **Depends On**: build-scan-clear, build-owner-map, build-trip-dedup
- **Assigned To**: cleanup-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Document the deny-but-don't-halt tradeoff, `denied_calls`, and the #1886
  decision criteria in the tool-budget feature doc; note SCAN clear + owner-map in
  the slot-lease doc; update the three docstrings.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-scan-clear, build-owner-map, build-trip-dedup, document-cleanup
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all success criteria including the #1868
  parity tests and the id-less dedup test.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass (touched suites) | `pytest tests/integration/test_tool_budget_enforcement.py tests/integration/test_out_of_domain_reclaim.py -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/session_watchdog.py agent/session_health.py agent/tool_budget.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/session_watchdog.py agent/session_health.py agent/tool_budget.py` | exit code 0 |
| Item 1: SCAN used | `grep -c "scan_iter" monitoring/session_watchdog.py` | output > 0 |
| Item 1: no KEYS in dedup clear | `sed -n '/def _clear_reclaim_dedup/,/^def /p' monitoring/session_watchdog.py \| grep -c "\.keys("` | match count == 0 |
| Item 2: stale-check no longer refetches | `sed -n '/def _maybe_emit_bridge_contract_stale/,/^def /p' agent/session_health.py \| grep -c "get_by_id"` | match count == 0 |
| Item 3: no `:None` dedup collapse | `grep -c "tripped_applied:None" agent/tool_budget.py` | match count == 0 |
| Item 3+4: denied_calls counter emitted | `grep -c "tool-budget:denied_calls" agent/tool_budget.py` | output > 0 |

## Open Questions

1. Item 2 scope: is threading a single owner→record map through `_reap_slot_leases`
   into both the stale-check and the Phase-2 loop the intended read-dedup, or would
   you prefer the narrower change of only removing the stale-check's second pass
   (leaving the Phase-2 loop's independent fetch untouched)? The plan proposes the
   former (removes both redundant reads) but it touches the reap loop's shape.
2. Item 4: is documenting the tradeoff + emitting the `denied_calls` counter now
   (decision tracked in #1886) the right handling, or do you want the raw counter
   deferred to #1886 as well so this plan is code-changes-to-items-1-3 only?
