status: FINAL (audit verdicts recorded 2026-07-16; removal actions recorded below — issue #2083)

# Popoto 1.8.0 Descriptor-Pollution / Index-Race Scar-Tissue Ledger

This is the inventory produced by the gating audit task (`audit-scar-tissue`,
issue [#2083](https://github.com/tomcounsell/ai/issues/2083)). It records a
per-defense verdict for every descriptor-pollution / index-race compensator in
`models/agent_session.py` and `models/session_lifecycle.py`, grounded in what
Popoto 1.8.0's `INDEX_SWAP_LUA` actually guarantees — confirmed by reading the
installed Popoto 1.8.0 source AND by fresh empirical repro scripts run against
a live Redis instance on 2026-07-16 (scripts in `/tmp`, not committed; every
scenario below was re-run in this pass, not inherited from the killed run).

**Gate status:** the #2086 hard gate is CLEARED — root-caused 2026-07-15 as a
mixed-version deploy artifact (1.8.0-writer / 1.7.1-reader choking on the raw
`{field}\x00idxset` pointer value), not the index race this audit concerns.

## TL;DR verdicts

| Cluster | Defense | File:Line (re-verified 2026-07-16) | Verdict |
|---|---|---|---|
| A | `__getattribute__` missing-field descriptor substitution | `models/agent_session.py` (formerly `:651-695`) | **REMOVED** (empirically dead — Popoto ≥1.6.1 default-fills absent fields at construction; see Finding 1 + Sentry tripwire below) |
| A | `_INT_FIELDS_BACKCOMPAT` set | `models/agent_session.py:636` | KEEP (still consumed by `__setattr__`'s write-path coercion; only its read-path consumer was removed) |
| A | `_DATETIME_FIELDS` + `__setattr__` datetime coercion | `models/agent_session.py:618`, `:697` | KEEP (guards malformed *values* on explicit write — a different mechanism than the lazy-load missing-field leak) |
| A | `_normalize_kwargs` `response_delivered_at` coercion (#929) | `models/agent_session.py:743` | KEEP (guards malformed values arriving via `__init__`, independent of lazy-load) |
| B1 | `_saved_field_values["status"]` backfill, site 1/2 | `models/session_lifecycle.py:474-475` (`finalize_session`) | **KEEP** — empirically load-bearing for pointer-less rows (Finding 2); shares disposition with B2 |
| B2 | `_saved_field_values["status"]` backfill, site 2/2 | `models/session_lifecycle.py:713-714` (`transition_status`) | **KEEP** — same mechanism/evidence as B1 |
| B3 | Defensive `srem`-across-`ALL_STATUSES` | `models/session_lifecycle.py:488-510` | **KEEP** — empirically load-bearing in the compound legacy scenario (Finding 4; the killed run's Open Question 1 is now RESOLVED with a live repro) |
| C | `_heal_future_updated_at`, `save()` updated_at stamp, `repair_indexes`, `cleanup_expired` | `models/agent_session.py:953,985,2110,2237` | OUT OF SCOPE — different bug classes (clock skew / rebuild-window / TTL), confirm-and-keep |

**Task 3 / 3b (pointer-establishing migration + active stale-count detector):
SKIPPED.** Both were conditional on a B-cluster REMOVE verdict. The verdict is
KEEP-all for Cluster B, so per the plan's No-Gos no migration and no detector
wiring were authored.

---

## INDEX_SWAP_LUA's actual guarantees (Task 1(a), re-verified)

`INDEX_SWAP_LUA` (`popoto/fields/indexed_field_mixin.py:99-128` in the
installed 1.8.0 dist) guarantees:

1. **Atomicity**: read-pointer → SREM-old → SADD-new → write-pointer →
   HSET-value is a single server-side Lua execution — no client-side
   interleave window for writes that go through it.
2. **Scope**: only fields mixing in `IndexedFieldMixin`. Confirmed at
   `popoto/models/base.py:1131,1292` ("Exclude IndexedFieldMixin fields —
   EVAL (INDEX_SWAP_LUA) owns their maintenance" on the plain-HSET path).
   On `AgentSession` there are FOUR `IndexedField`s — `status` (:145),
   `task_type` (:184), `claude_session_uuid` (:230), `claude_pid` (:262).
   (Correction to the plan's freshness note, which called `status` the only
   one; `status` is the only IndexedField any Cluster B defense concerns.)
   Every field in Cluster A's defenses (`exit_returncode`,
   `tool_timeout_count_*`, `response_delivered_at`, `last_heartbeat_at`,
   etc.) is a plain `Field`/`IntField`/`DatetimeField` — entirely outside
   INDEX_SWAP_LUA's scope.
3. **Pointer-dependence**: the atomic SREM of the old Set requires the
   server-authoritative `{field}\x00idxset` pointer in the model hash. For
   rows without one, the Lua falls back to the client-supplied
   `legacy_old_set` hint (`ARGV[6]`, sourced from `_saved_field_values`) —
   this is the sole channel through which B1/B2's backfill remains
   load-bearing.
4. **What it does NOT guarantee**: freshness of the *value* being written. A
   stale full save writes a stale value atomically, and the index follows
   the (stale) value. Pointer atomicity ≠ value freshness.

## Cluster A — descriptor-leak defenses (plain, non-indexed Fields)

### Finding 1 — the missing-field descriptor leak does NOT reproduce under Popoto 1.8.0

Popoto's `_create_lazy_model` (`popoto/models/encoding.py`, the
"Initialize defaults for fields absent from the hash" loop, present since
1.6.1 / upstream #380) default-fills every field absent from the Redis hash
at construction time, before any attribute access. Fresh empirical repro
(2026-07-16, live Redis):

1. Saved a real `AgentSession` row, then HDEL'd `tool_timeout_count_internal`
   (IntField) and `response_delivered_at` (DatetimeField) from its hash —
   simulating a legacy row written before those fields existed.
2. Lazy-loaded a fresh object via `AgentSession.query.filter(...)`; confirmed
   the keys are genuinely absent from the hash.
3. Read both fields (a) through `AgentSession.__getattribute__` (the defense)
   and (b) through `popoto.models.base.Model.__getattribute__` directly,
   bypassing the override: **both paths returned scalars (`0` / `None`),
   never a descriptor object.**

**Action taken (Task 4): the `__getattribute__` override was removed.** Its
sole job was substituting the descriptor object on missing-field reads — a
case Popoto itself has handled since 1.6.1. The committed regression test
(`tests/unit/test_agent_session.py::TestClusterARemoveCandidateEmpiricalRegression`)
reproduces the original #1099/#1172 scenario and stays green with the
override deleted; if a future Popoto regresses the default-fill, that test
goes red. Passive tripwire: Sentry ledger issue (link below).

`_INT_FIELDS_BACKCOMPAT` itself was NOT removed — `__setattr__` still
consumes it to coerce malformed values written explicitly (bad type → 0).

### `__setattr__` coercion and `_normalize_kwargs` — KEEP (different mechanism, not disproven)

Finding 1 is scoped to the *missing-key read* case. It does not cover
coercion of malformed values that are actually present or explicitly
assigned (e.g. `response_delivered_at` as an unparseable ISO string, an
epoch float via a non-`__init__` write path — issues #929, #1172).
`decode_lazy_field` decodes msgpack directly into `_decoded_fields`
(bypassing `__setattr__`), so `__setattr__`'s coercion fires only for
explicit assignment — which happens at construction via `_normalize_kwargs`
and in hooks/session_health write paths. No repro showed this mechanism
dead; NOT subsumed by 1.8.0 (INDEX_SWAP_LUA never touches plain-field value
coercion). **KEEP**, with in-code keep-comments referencing #2083.

## Cluster B — status-index defenses (three sites)

### Finding 2 — B1/B2 backfill is empirically load-bearing for pointer-less rows

Fresh repro (2026-07-16, live Redis): created a row, stripped the
`status\x00idxset` pointer hash field (simulating a pre-1.8.0 row),
lazy-loaded a fresh object (whose `_saved_field_values` contains no
`status`), transitioned `pending → running` via a plain `save()`:

- **WITHOUT the backfill:** row stranded in BOTH `pending` and `running`
  index Sets (Lua has no pointer and an empty hint, so it SADDs without
  SREMing).
- **WITH the backfill** (`_saved_field_values["status"] = "pending"`): clean
  swap — row in `running` only.

**Verdict: B1/B2 KEEP.** They remain the only source of the
`legacy_old_set` hint for rows that predate the 1.8.0 pointer. Removal
would require the conditional pointer-establishing migration (plan Task 3)
plus proof that no pointer-less row can exist; neither was pursued (KEEP-all
default). B1 and B2 share one disposition — never remove one without the
other (enforced by the structural guard test
`test_both_backfill_sites_move_together`).

### Finding 3 — steady-state stale-object full save (#950): B3 makes no observable difference

Fresh repro of the plan's Task 1(d) scenario (2026-07-16, live Redis), run
WITH B3 active and WITHOUT it (client-side `srem` monkeypatched to a no-op —
the server-side Lua SREM is unaffected, exactly simulating B3's deletion):

1. P1 finalizes the row to `completed` → member exclusively in `completed`.
2. P2, holding a stale in-memory object (`status="running"`), does a full
   `save()` → member atomically moved to `running` and removed from
   `completed` — **never in two Sets**, identical WITH and WITHOUT B3. The
   clobbering write goes through INDEX_SWAP_LUA like any other write; the
   pointer-driven SREM/SADD is unconditional.
3. A repair re-finalize lands the row exclusively in `completed` — again
   identical WITH and WITHOUT B3.

So in the steady state (row has a pointer), pointer atomicity fully absorbs
the #950 clobber-then-repair sequence: the value can go stale, but the index
always matches the value, and any subsequent legitimate write self-heals.
The plan's "value-freshness" framing for B3 is real but is absorbed by
pointer-following for pointer-bearing rows.

### Finding 4 — B3 IS load-bearing in the compound legacy scenario (Open Question 1 RESOLVED)

The killed run left this as an open question; this pass reproduced it
(2026-07-16, live Redis):

1. Legacy row (pointer stripped), status `pending`.
2. Stranding write: lazy object with no hint transitions to `running` via
   plain `save()` → row stranded in `{pending, running}` (Finding 2's
   failure mode — i.e. a stranding that already happened, whatever its
   source).
3. `finalize_session(..., "completed")` on a fresh read:
   - **WITH B3:** row ends exclusively in `completed` — the blind sweep
     scrubs the stray `pending` membership.
   - **WITHOUT B3:** row ends in `{completed, pending}` — **still
     stranded.** The B1 backfill's hint only SREMs the *current on-disk*
     status Set (`running`); neither the pointer nor the hint knows about
     the stray `pending` membership. Only B3's blind sweep repairs it.

**Verdict: B3 KEEP — no longer "default KEEP on ambiguity" but empirically
confirmed load-bearing.** B3 is the only repair path for pre-existing
strandings (legacy rows, crashed mid-write rows, any historical index
drift) at the moment a session reaches a terminal state. Its rationale is
repair-of-prior-drift, entangled with the same legacy-pointer gate as
B1/B2 — if B1/B2 are ever removed post-migration, B3 must be re-evaluated
at the same time, not independently.

The committed red-state test
(`tests/unit/test_agent_session_index_corruption.py::TestStaleFullSaveRedState950`)
pins both findings: the steady-state arm (no stranding, WITH and WITHOUT
B3) and the compound-legacy arm (stranded WITHOUT B3, clean WITH it).

## Cluster C — out of scope, confirm-and-keep

| Symbol | File:Line | Bug class |
|---|---|---|
| `_heal_future_updated_at` | `models/agent_session.py:985` | Clock skew (#1645/#1817) — detection only |
| `save()` `updated_at` UTC stamp override | `models/agent_session.py:953` | Same, write-side |
| `repair_indexes` | `models/agent_session.py:2110` | `rebuild_indexes()` class-set delete/re-add window (#1720); drift commits `1aedc8a4e` (#2101/#2102 A1 rebuild guard) and `d105b33e5` (batched stale-index scans) touched ONLY this Cluster-C site — re-verified, no Cluster A/B impact |
| `cleanup_expired` | `models/agent_session.py:2237` | TTL-based row cleanup, unrelated |

None reference `_saved_field_values`, `INDEX_SWAP_LUA`, or the status index
maintenance path. `repair_indexes()`'s `(stale_count, rebuilt_count)` return
remains the building block for a future active stale-count detector if any
Cluster B removal ever ships (not now — Task 3b skipped).

## Stale-comment corrections

`_heal_descriptor_pollution` never existed as a method (grep-confirmed). The
two stale comments referencing it (`models/agent_session.py:236`, `:335`,
pre-drift numbering) were corrected in Task 4 to describe the real
mechanism: Popoto's `_create_lazy_model` default-fill plus `__setattr__`'s
write-path coercion.

## Removed-defenses Sentry ledger (passive regression tripwire)

| Removed defense | Sentry tripwire | Signature |
|---|---|---|
| `AgentSession.__getattribute__` missing-field descriptor substitution (#1099/#1172 read-path arm) | [VALOR-E1](https://yudame.sentry.io/issues/7604718329/) (dup: [VALOR-D0](https://yudame.sentry.io/issues/7599482038/)) | `TypeError: '<=' not supported between instances of 'str' and 'int'` in `_agent_session_tool_timeout_check` (`agent/session_health.py`) |

One issue per removed defense (plan Open Question 2 resolved as per-defense
mapping — only one removal shipped). The removed defense's failure mode, if it
regressed, is a **type-comparison error in the health-check read path**: a
non-scalar (descriptor object) or mistyped value reaching `exit_returncode` /
`tool_timeout_count_*` where the OOM / tool-timeout detectors do `int`
comparisons. VALOR-E1 is the exact signature that family produces
(`'<=' not supported between 'str' and 'int'` in `_agent_session_tool_timeout_check`)
— a regression re-surfaces there. Generic Popoto field/descriptor leakage of
the same class is tracked under [VALOR-35](https://yudame.sentry.io/issues/7451457999/)
(`Validation on [created_at] Field failed`) and
[VALOR-36](https://yudame.sentry.io/issues/7451458013/) (a `SortedField` object
appearing where a value was expected). Because these tripwires are passive
(they only fire if a regression throws), they are paired with the committed
regression test
(`tests/unit/test_agent_session.py::TestClusterARemoveCandidateEmpiricalRegression`),
which is the active guard: it reads the formerly-defended fields both through
`AgentSession` and through Popoto's base `Model.__getattribute__`, asserting a
scalar from both, so a future Popoto default-fill regression goes red in CI
before it can reach production.

## Open Questions (default: KEEP)

1. ~~Compound legacy-no-pointer + no-hint scenario for B3~~ — **RESOLVED**
   (Finding 4): reproduced live; B3 is load-bearing there. KEEP.
2. **Fraction of live rows with an established pointer** — still unmeasured;
   only relevant if a future task pursues B1/B2 removal (would decide
   whether the conditional migration is necessary or organic traffic since
   PR #2081 already established pointers everywhere).
3. **Cluster A `__setattr__`/`_normalize_kwargs` scope** — not disproven;
   they guard explicit-write value coercion (#929/#1172), a materially
   different mechanism from the removed missing-field read arm. KEEP.
