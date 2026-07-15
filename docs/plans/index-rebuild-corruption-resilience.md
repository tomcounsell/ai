---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2086
last_comment_id:
revision_applied: false
revision_applied_at:
---

# Index Rebuild Corruption Resilience — one un-decodable hash must not blind the whole queue

## Problem

A single corrupted `AgentSession` Redis hash silently makes **every** AgentSession
record invisible to queries. On 2026-07-14 (~06:11–06:40 UTC) a session crashed with a
msgpack error — `unpack(b) received extra data` — and afterward `AgentSession.query.all()`
returned **0** records **without raising**, while 11 healthy AgentSession hashes
demonstrably still existed in Redis (`repair_indexes` reported `sessions_rebuilt=11,
cleaned=0 corrupt`). Dashboards and the `valor-session` CLI showed an empty queue while the
data existed. Redis was not restarted or flushed (17.8-day uptime); no cleanup deletion ran.

**Current behavior (root cause — confirmed in library + repo code):**

`Model.rebuild_indexes()` (popoto `models/base.py:2741`, external dep, version 1.8.0) is
**delete-first and non-atomic**:

1. `base.py:2779` — unconditionally `DELETE $Class:AgentSession` (the class set) *before* any
   re-add.
2. `base.py:2819` — `scan_iter("AgentSession:*")` over the raw hash keyspace.
3. `base.py:2839` — for each hash, eagerly `decode_popoto_model_hashmap(cls, redis_hash)`
   (`lazy=False`). This is the one place that eagerly msgpack-decodes **every field of every
   record**.
4. `base.py:2847` — re-add each key to the class set via a **pipelined `sadd`**, flushed only
   in batches at `base.py:2869-2876`.

`decode_popoto_model_hashmap` (popoto `models/encoding.py:328-334`) decodes fields in an
**unguarded dict comprehension** of `msgpack.unpackb(...)` — no `try/except`. A single field
whose stored bytes are malformed raises `msgpack.exceptions.ExtraData` (`"unpack(b) received
extra data"`). That raise propagates out of the scan loop and out of `rebuild_indexes()`
**after the class set was deleted (step 1) but before the pipeline flush (step 4)** — so the
class set is left **empty**. Every subsequent `AgentSession.query.all()` reads
`smembers($Class:AgentSession)` (`query.py:1793-1797`), finds an empty set, and returns `[]`
cleanly — **zero records, zero exception**.

This reconciles every observed fact:
- `query.all() == 0, no exception` → the class set was empty at query time (not a swallowed
  decode; popoto's query path would *raise*, not return `[]`, on a corrupt hash — see Data
  Flow).
- `repair_indexes` found 11 → it SCANs the raw hash keyspace (`base.py:2819`), independent of
  the empty class set.
- `cleaned=0 corrupt` → `cleanup_corrupted_agent_sessions()` (`agent/session_health.py:4564`)
  begins with `list(AgentSession.query.all())`, received `[]` (empty set, no raise), so its
  corrupted-record loop never ran.

The periodic `agent-session-cleanup` / `redis-index-cleanup` reflections call
`repair_indexes()` → `rebuild_indexes()` every ~300s, so the empty-class-set state is
re-created on every tick that scans the corrupt hash — a persistent form of the transient
delete→re-add window already documented for issue #1720
(`tools/valor_session.py:60`, `tools/sdlc_stage_query.py:74`).

**Why it matters:** one corrupt record — a routine, expected failure mode that already occurs
a handful of times per day — takes the **entire** session queue offline for reads. The worker
loses its view of pending/running work, dashboards and CLI report zero sessions, and recovery
tooling that treats "empty" as truth makes wrong decisions. This is the most severe
data-integrity failure in the substrate: it converts single-record corruption into total
queue blindness.

**Desired outcome:** a single un-decodable hash is **skipped, logged loudly, and quarantined**;
all healthy records remain indexed and visible. The class set is **never** left empty because
of one bad record. Index rebuild becomes atomic-on-failure (build-then-swap), and the critical
scan callers surface "N records skipped due to decode corruption" instead of silently
reporting an empty queue.

## Recon Summary

Recon performed inline against `main` @ HEAD (2026-07-15) via direct library + repo code reads
and an independent Explore-agent trace (both converged on the same root cause with identical
file:line citations).

**Confirmed:**
- Popoto is an **external** uv-installed wheel at
  `.venv/lib/python3.14/site-packages/popoto/`, **version 1.8.0** (runtime `popoto.__version__`;
  a stale `1.7.1` in `~/Library/Python/3.12` user-site is NOT what imports). Not vendored, not
  editable — the library files cannot be edited as a repo change.
- `rebuild_indexes()` deletes the class set first (`base.py:2779`) and re-adds via a batched
  pipeline (`base.py:2847`, flush `2869-2876`); eager decode at `base.py:2839`.
- `decode_popoto_model_hashmap` has an unguarded `msgpack.unpackb` comprehension
  (`encoding.py:328-334`, lazy KeyField path `encoding.py:439-443`/`490`).
- Query hydration is unguarded in BOTH the sync path (`query.py:2688-2694`,
  `get_many_objects`) and the async twin (`query.py:3079-3084`); `Query.all()` resolves
  members from `smembers($Class:...)` (`query.py:1793-1797`).
- Repo callers that mask empty-as-truth: `AgentSession.repair_indexes()`
  (`models/agent_session.py:2102-2143`), `cleanup_corrupted_agent_sessions()`
  (`agent/session_health.py:4564`), `_heal_future_updated_at()` (`models/agent_session.py:1004-1008`),
  and the `try/except: return None/[]` lookup wrappers (`get_by_id`, etc.).

**Revised:** none.

**Pre-requisites:** none.

**Dropped:** none.

## Freshness Check

**Baseline commit:** `main` @ HEAD 2026-07-15 (post #2098/#2089/#2044/#2088 substrate merges).
**Issue filed at:** 2026-07-14T~06:40Z. **Disposition: Unchanged.**

- Library file:line references (`base.py:2741/2779/2839/2847`, `encoding.py:328-334`,
  `query.py:1793-1797/2688-2694/3079-3084`) re-verified against the actually-imported popoto
  1.8.0 — all exact.
- Repo references (`models/agent_session.py:2102-2143`/`1004-1008`,
  `agent/session_health.py:4564`) re-verified — exact.
- **Overlap (surfaced, not colliding):** `#2083` (`docs/plans/popoto-descriptor-pollution-audit.md`,
  status Ready, branch `session/popoto-descriptor-pollution-audit`) audits Popoto 1.8.0
  descriptor-pollution / index-race scar tissue at the **model save/index** layer. This plan
  is disjoint: it targets **read-path / rebuild decode resilience** (making rebuild + query
  hydration survive one un-decodable hash). Coordination: if #2083 lands first and changes the
  save/encode path, re-confirm the corrupt-hash reproduction still holds. No file-level
  collision (this plan owns `repair_indexes` / a new resilient rebuild + a hydration helper;
  #2083 owns the `_INT_FIELDS_BACKCOMPAT` / `_saved_field_values` save-side scar tissue).
- Sibling to #2098/#2088 substrate hardening (part of #1818 resilience umbrella).

## Research

No relevant *external* findings needed — the root cause is fully determined from the imported
popoto 1.8.0 source and repo code. msgpack's `ExtraData` is the standard "trailing bytes after
a complete object" error; the catch must cover the `msgpack.exceptions.UnpackException` family
(`ExtraData`, `FormatError`, `StackError`) plus `ValueError`/`TypeError` from `decode_custom_types`.

## Data Flow

1. **Corruption onset:** a crashing session leaves one `AgentSession:<id>` hash with a field
   whose msgpack bytes have trailing data (`unpack(b) received extra data`). (How the trailing
   bytes are written is out of scope — likely a partial/interleaved write during the crash;
   #2083 owns the save-side audit.)
2. **Reflection tick (~300s):** `agent-session-cleanup` → `cleanup_corrupted_agent_sessions()`
   (`session_health.py`) → `AgentSession.repair_indexes()` (`agent_session.py:2142`) →
   `cls.rebuild_indexes()` (popoto).
3. **Class set deleted:** `base.py:2779` `DELETE $Class:AgentSession` executes immediately.
4. **Abort mid-rebuild:** the scan reaches the corrupt hash; `base.py:2839`
   `decode_popoto_model_hashmap` raises `ExtraData`; the pipelined `sadd` re-adds
   (`base.py:2847`) buffered so far are **discarded** (never `execute()`d) → class set left
   empty.
5. **Queue blind:** every later `AgentSession.query.all()` → `smembers($Class:AgentSession)` =
   ∅ → returns `[]`, no exception (`query.py:1793-1797`, `2688-2694`).
6. **Masking cascade:** `cleanup_corrupted_agent_sessions` reads `[]` → `cleaned=0`; dashboards
   / CLI show zero sessions; `get_by_id`/`_heal_*` swallow and return None/0/[].
7. **Desired:** step 4 skips+quarantines+logs the one bad hash and completes; step 3 becomes
   build-then-swap so the live class set is never empty on failure; step 5 sees all 11 healthy
   members; step 6 surfaces "1 record quarantined (decode corruption)".

## Solution

### Design decision: repo-owned corruption-tolerant rebuild + resilient hydration helper

Popoto 1.8.0 is external and non-editable, so the durable fix lives in this repo. Two disjoint
gaps, fixed together:

**A. Corruption-tolerant, atomic-on-failure index rebuild (primary).** Replace
`AgentSession.repair_indexes()`'s blind delegation to popoto's delete-first `rebuild_indexes()`
with a repo-owned rebuild that:
1. SCANs the raw hash keyspace (`AgentSession:*`) — same discovery as popoto.
2. Decodes each hash inside `try/except (msgpack.exceptions.UnpackException, ValueError,
   TypeError)`. On failure: `logger.error` naming the exact `redis_key` and the decode error,
   increment a `quarantined` counter, append the key to a quarantine report, and `continue`
   — **never abort**.
3. Builds the new class-set membership (and rebuilds the field/sorted/composite indexes) for
   **healthy** records only, into a **temporary** class-set key, then atomically `RENAME`s the
   temp key onto `$Class:AgentSession` at the end. The live class set is therefore never
   deleted-then-left-empty: on any mid-rebuild failure the previous (or freshly built) set
   stays intact. (If a full popoto-parity rebuild of all secondary indexes is too large to
   reimplement safely, the minimal viable variant rebuilds only the class set atomically via
   temp-key+rename and still calls popoto for the field indexes — see Open Questions.)
4. Returns `(stale, rebuilt, quarantined)` so `cleanup_corrupted_agent_sessions` and the
   dashboard can surface corruption counts instead of a silent empty queue.

**B. Resilient query hydration for the critical scan callers (secondary / defense-in-depth).**
Because popoto's `get_many_objects` comprehension (`query.py:2688-2694`) aborts the *whole*
result if a corrupt hash is present in the class set, add a repo helper
`AgentSession.query_all_resilient()` (or `iter_hydrated(...)`) that reads the member keys and
decodes each hash under `try/except`, skipping+logging the bad ones and returning all healthy
records. Route the substrate-critical callers through it: `cleanup_corrupted_agent_sessions()`
(`session_health.py:4564`) and the dashboard's session listing. This guarantees that even a
corrupt hash *inside* the class set can no longer blind those consumers.

The repo-override altitude (not a global monkeypatch) is chosen to bound blast radius to
`AgentSession`, the only model with this failure history. A global monkeypatch of
`Model.rebuild_indexes` / `Query.get_many_objects` is weighed in Open Questions.

### Key Elements

- `AgentSession.rebuild_indexes()` (or a renamed repo-owned rebuild called by `repair_indexes`):
  corruption-tolerant, temp-key+rename atomic swap, per-record quarantine, `(stale, rebuilt,
  quarantined)` return.
- `AgentSession.query_all_resilient()`: per-record guarded hydration for critical scan callers.
- Loud, actionable logging (`logger.error` with exact `redis_key`) + a project-scoped metric
  (`{project_key}:index-rebuild:quarantined`) so an un-decodable hash is *visible*, not silent.
- Callers updated to surface quarantine counts: `cleanup_corrupted_agent_sessions()`,
  dashboard session listing.

### Race Conditions

- **Rebuild vs concurrent save (issue #1720 window).** The temp-key+rename approach *shrinks*
  the empty-class-set window to a single atomic `RENAME` (vs the current delete→batched-readd
  gap). A concurrent `save()` that `sadd`s to the live class set between temp-build and rename
  could be lost by the rename. Mitigation: the existing bounded-retry read defense (#1720,
  `valor_session.py:60`) still covers the sub-millisecond rename; and `repair_indexes` runs on
  a 300s reflection cadence, not in the hot save path. Confirm the rename cannot drop a
  concurrently-saved key, or fold concurrent members in before the rename. (Open Question.)

## Failure Path Test Strategy

### Corruption Handling Coverage
- [ ] Seed Redis (test-scoped `project_key`) with N healthy AgentSession hashes + 1 hash whose
  field bytes are deliberately corrupt (`msgpack.packb(x) + b"\xff"` → `ExtraData` on decode).
  Call the repo rebuild; assert: (a) it does NOT raise, (b) the class set contains all N
  healthy keys after rebuild, (c) `query.all()` returns N healthy records, (d) the corrupt key
  is quarantined+logged and the return `quarantined == 1`.
- [ ] Assert the live `$Class:AgentSession` is **never observed empty** across the rebuild:
  the atomic swap means a concurrent reader sees either the old or new set, never ∅ (drive a
  read mid-rebuild or assert the delete-first path is gone).
- [ ] `query_all_resilient()` with a corrupt hash present *in the class set*: returns all
  healthy records, skips+logs the bad one, does not raise.

### Empty/Invalid Input Handling
- [ ] All-healthy rebuild: `quarantined == 0`, membership unchanged, parity with popoto's count.
- [ ] Empty keyspace: rebuild returns `(0,0,0)`, no crash, class set empty is correct (no hashes).

### Error State Rendering
- [ ] Assert the `logger.error` names the exact corrupt `redis_key` and the metric increments.
  No user-facing surface beyond logs/dashboard count.

## Test Impact

- [ ] `tests/unit/test_agent_session_indexes.py` (or the existing repair_indexes test file, TBD
  by recon during build) — UPDATE (additive): add corrupt-hash rebuild resilience tests above.
- [ ] `tests/unit/` health-check tests that assert `cleanup_corrupted_agent_sessions` behavior —
  UPDATE if they assume `query.all()` is the entry point (now `query_all_resilient`). Audit at
  build time; most assert on the corrupted-record loop, unaffected by the hydration swap.
- [ ] No existing test currently reproduces the empty-class-set-on-corrupt-hash path — this is
  net-new coverage for a confirmed-in-production defect.

## Documentation

- [ ] Create `docs/features/index-rebuild-corruption-resilience.md` describing the delete-first
  non-atomic rebuild hazard, the temp-key+rename fix, the quarantine behavior, and the
  `query_all_resilient` helper. Cross-link from `docs/features/agent-session-queue.md` and the
  session-recovery audit (`docs/plans/session-recovery-observation-audit.md` §21 non-atomic
  index repair).
- [ ] Update `docs/features/agent-session-model.md` (or wherever `repair_indexes`/`is_ledger`
  index behavior is documented) with the new return tuple and quarantine semantics.

## Update System

No `scripts/update/run.py` change required. **Popoto schema:** no model *schema* change (no new
fields, no field-type change) — this is control-flow/index-rebuild logic only, so no
`scripts/update/migrations.py` entry is required. If the fix chooses to *delete* un-decodable
quarantined hashes (Open Question), that is a runtime reap, not a schema migration. Confirm at
build time that no migration is needed.

## Agent Integration

No agent integration required — this is a worker/model-internal resilience fix. No new CLI
entry point, no `.mcp.json` / MCP change, no bridge import. The existing `valor-session`
CLI and dashboard automatically benefit (they stop showing a false-empty queue). Optionally
expose the `quarantined` count in `curl localhost:8500/dashboard.json` (surfacing, not new
integration).

## No-Gos (Out of Scope)

- Editing popoto library files in `.venv` (external wheel — would be lost on reinstall).
- Fixing the *save-side* cause of the corrupt bytes — that is #2083's descriptor-pollution
  audit. This plan makes rebuild/read **survive** corruption; it does not prevent the write.
- A global monkeypatch of all popoto models — surfaced as an Open Question, not defaulted.

## Rabbit Holes

- **Reimplementing all of popoto's secondary-index rebuild.** If full parity (sorted/geo/
  composite indexes) is heavy, scope the atomic-swap guarantee to the **class set** (the thing
  that blinds `query.all()`) and delegate field-index rebuild to popoto per healthy record.
- **Deleting un-decodable hashes.** An un-decodable hash cannot be removed via
  `instance.delete()` (no instance can be constructed). Leaving it un-indexed + loudly alerted
  already un-blinds the queue; a sanctioned corrupt-hash removal path is a follow-up
  (Open Question), not required for the fix.
- **Tuning reflection cadence.** The 300s rebuild cadence is not the bug; do not touch it.

## Risks

### Risk 1: Temp-key+rename drops a concurrently-saved member
**Impact:** a `save()` that `sadd`s to the live class set during the rebuild's temp-build window
could be lost by the final `RENAME`.
**Mitigation:** `repair_indexes` runs on a 300s reflection cadence, not the hot path; the rename
is atomic and sub-millisecond; fold any live-set members into the temp set immediately before
rename, or `SUNIONSTORE` temp+live→live. Validate under a concurrent-save test. (Open Question.)

### Risk 2: Reimplementing rebuild diverges from popoto's index semantics
**Impact:** a hand-rolled rebuild could miss a secondary-index type popoto maintains, leaving a
stale index.
**Mitigation:** prefer the minimal variant — atomic class-set swap for the blinding fix, and
call popoto's per-record `on_save`/index hooks for healthy records so field-index logic is not
re-derived. Parity test against popoto's rebuilt count for the all-healthy case.

### Risk 3: #2083 changes the encode/save path
**Impact:** if #2083 lands first, the corrupt-bytes reproduction or decode error type could shift.
**Mitigation:** the catch covers the whole `msgpack.exceptions.UnpackException` family +
`ValueError`/`TypeError`; the test pins behavior and fails loudly if the type changes. Note the
coupling in the PR.

## Success Criteria

- [ ] With one corrupt AgentSession hash present, `AgentSession.repair_indexes()` /
  the repo rebuild completes without raising, and `AgentSession.query.all()` returns **all
  healthy** records (not 0).
- [ ] The live `$Class:AgentSession` is never left empty due to one un-decodable hash (atomic
  temp-key+rename; no delete-then-abort window).
- [ ] The corrupt hash is logged at `error` with its exact `redis_key` and counted in a
  `quarantined` metric; `cleanup_corrupted_agent_sessions` / dashboard surface the count.
- [ ] `query_all_resilient()` returns all healthy records when a corrupt hash is in the class
  set, skipping+logging the bad one.
- [ ] New regression test reproduces the empty-class-set-on-corrupt-hash scenario and asserts
  the queue stays visible.
- [ ] No raw-Redis deletion of Popoto-managed keys is introduced (temp-key + `RENAME` /
  `SUNIONSTORE` are index-set operations, not record deletes; confirm they are sanctioned or
  routed through an approved helper).
- [ ] `python -m ruff check` / `format --check` clean; targeted tests pass.

## Open Questions

1. **Rebuild altitude:** full repo reimplementation of `rebuild_indexes` (all secondary index
   types, atomic) vs minimal (atomic class-set swap + delegate field indexes to popoto per
   healthy record)? Recommendation: minimal — it fixes the blinding with the smallest blast
   radius.
2. **Blast radius:** `AgentSession`-only repo override vs a global import-time monkeypatch of
   `Model.rebuild_indexes` + `Query.get_many_objects` (protects every popoto model). Recommendation:
   AgentSession-only now; file a follow-up for the global monkeypatch if other models show the
   failure.
3. **Un-decodable hash disposition:** leave un-indexed + alert (queue un-blinded, corrupt hash
   lingers) vs a sanctioned corrupt-hash removal path (cannot use `instance.delete()`).
   Recommendation: leave + alert now; follow-up for a removal path.
4. **Concurrent-save vs rename (Risk 1):** confirm the atomic swap cannot drop a live save, or
   `SUNIONSTORE` temp+live→live before rename.
