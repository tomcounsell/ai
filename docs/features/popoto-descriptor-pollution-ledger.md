status: DRAFT (Task 1 of #2083 — read-only audit, gates all further work)

# Popoto 1.8.0 Descriptor-Pollution / Index-Race Scar-Tissue Ledger

This is the inventory produced by the gating audit task (`audit-scar-tissue`,
issue [#2083](https://github.com/tomcounsell/ai/issues/2083)). It records a
per-defense verdict for every descriptor-pollution / index-race compensator in
`models/agent_session.py` and `models/session_lifecycle.py`, grounded in what
Popoto 1.8.0's `INDEX_SWAP_LUA` actually guarantees (confirmed by reading the
installed Popoto 1.8.0 source and by empirical repro scripts run against a
real Redis instance during this audit — no removal or code change was made in
this task).

**This doc is a DRAFT.** It will be finalized (verdict → action, Sentry ledger
links, active-detector location) by the documentation task after any removal
work ships. Nothing in `models/` was changed to produce this draft.

## TL;DR verdicts

| Cluster | Defense | File:Line | Verdict |
|---|---|---|---|
| A | `_INT_FIELDS_BACKCOMPAT` + `__getattribute__` descriptor substitution | `models/agent_session.py:622-687` | **REMOVE-CANDIDATE** (empirically dead for the missing-field case — see Finding 1) |
| A | `_DATETIME_FIELDS` + `__setattr__` datetime coercion | `models/agent_session.py:610-620`, `:689-720` | KEEP (guards malformed *values* on write, a different mechanism than the lazy-load leak) |
| A | `_normalize_kwargs` `response_delivered_at` coercion (#929) | `models/agent_session.py:742-903` | KEEP (guards malformed values arriving via `__init__`, independent of lazy-load) |
| B1 | `_saved_field_values["status"]` backfill, site 1/2 | `models/session_lifecycle.py:474-475` (`finalize_session`) | KEEP, gated — shares disposition with B2 |
| B2 | `_saved_field_values["status"]` backfill, site 2/2 | `models/session_lifecycle.py:713-714` (`transition_status`) | KEEP, gated — shares disposition with B1 |
| B3 | Defensive `srem`-across-`ALL_STATUSES` | `models/session_lifecycle.py:494-510` | KEEP, gated — **entangled with the same legacy-pointer gate as B1/B2**, not an independent "value-freshness" defense (see Finding 3, a correction to the plan's working hypothesis) |
| C | `_heal_future_updated_at`, `save()` updated_at stamp, `repair_indexes`, `cleanup_expired` | `models/agent_session.py:945,977,2102,2146` | OUT OF SCOPE — confirmed a different bug class (clock skew / rebuild-window), confirm-and-keep |

No code was removed in this task. "REMOVE-CANDIDATE" means the next task
(`build-regression-tests`) should write the red-state regression test and let
the actual removal task decide; "KEEP, gated" means removal requires the
conditional pointer-establishing migration described in the plan and is not
authorized by this audit alone.

---

## Cluster A — descriptor-leak defenses (plain, non-indexed Fields)

### Inventory

| Symbol | File:Line |
|---|---|
| `_DATETIME_FIELDS` (set) | `models/agent_session.py:610-620` |
| `_INT_FIELDS_BACKCOMPAT` (set) | `models/agent_session.py:622-636` |
| `__getattribute__` override | `models/agent_session.py:643-687` |
| `__setattr__` override | `models/agent_session.py:689-732` |
| `_normalize_kwargs` classmethod (`response_delivered_at` coercion, #929) | `models/agent_session.py:734-903` |

Repo-wide grep confirms `AgentSession` is the only Popoto model in this repo
carrying these overrides; no sibling models replicate the pattern (matches
plan's No-Gos item "confirm no siblings").

`_heal_descriptor_pollution` does **not exist** as a method anywhere in the
codebase — confirmed by grep. It survives only as stale prose in two
comments: `models/agent_session.py:236` and `models/agent_session.py:335`
(both say "`_heal_descriptor_pollution` walks fields generically" — should
be corrected in the removal/docs task to name the actual mechanism, i.e. the
`__getattribute__`/`__setattr__` overrides plus Popoto's own default-filling
in `_create_lazy_model`).

### Finding 1 — the missing-field descriptor leak does NOT reproduce under Popoto 1.8.0 (contradicts the plan's spike-1 premise)

The plan's Research/Spike-1 section hypothesized "the lazy-load path that
produces the descriptor leak (bug class 2) still exists in 1.8.0", citing
`popoto/models/base.py:728`. That citation is real but describes a
*different* code path than the one that caused #1099/#1172/#1185: it
documents lazy decoding of fields **present** in the Redis hash
(`_lazy_fields` / `decode_lazy_field`), not fields **absent** from the hash
entirely (the actual #1099 scenario — a field added to the model after a row
was written).

Reading the installed Popoto 1.8.0 source (`popoto/models/encoding.py:438-464`,
`_create_lazy_model`) shows Popoto **already defaults absent fields at
construction time**, before any attribute access:

```python
# models/encoding.py:456-464 (installed popoto 1.8.0)
for field_name, field in model_class._meta.fields.items():
    if field_name in instance._lazy_fields: continue
    if field_name in instance._decoded_fields: continue
    default_value = field.default() if callable(field.default) else field.default
    object.__setattr__(instance, field_name, default_value)
```

This matches a comment already present in `agent_session.py:622-627`, which
this audit had been told to distrust by name (`_heal_descriptor_pollution`)
but which turns out to be accurate in substance: *"Popoto v1.6.1 fixed the
lazy-load descriptor leak that originally motivated this set (issue #1099)…
the guards stay as belt-and-suspenders."* The fix predates the 1.8.0 upgrade
entirely (landed at 1.6.1) — it is not something #2081 changed one way or
the other.

**Empirical repro** (run against a live Redis instance in this audit, not
committed — script discarded from `/tmp` after the run):
1. Created a real `AgentSession` row, saved it normally.
2. Deleted the `tool_timeout_count_internal` (IntField) and
   `response_delivered_at` (DatetimeField) hash keys directly from Redis —
   simulating a legacy row written before those fields existed.
3. Re-fetched via `AgentSession.query.filter(...)`. Confirmed via
   `_lazy_fields` inspection that both deleted keys are genuinely absent
   (not just lazily-undecoded).
4. Read the fields two ways:
   - Through `AgentSession`'s own `__getattribute__` (the defense): `0`
     (int) and `None` respectively — expected.
   - Through `popoto.models.base.Model.__getattribute__` directly, **bypassing
     AgentSession's override entirely**: **also `0` and `None`** — Popoto's
     own default-fill already produced scalars, never the raw descriptor
     object.

**Verdict: `_INT_FIELDS_BACKCOMPAT` + `__getattribute__`'s descriptor
substitution is REMOVE-CANDIDATE for the missing-field case** — the bug it
guards against does not reproduce under the currently-installed Popoto
(and, per the in-repo comment, has not reproduced since 1.6.1, independent
of the 1.8.0 upgrade this issue is nominally about). This is a genuine
correction to the plan's Cluster-A-defaults-to-KEEP premise, not an
ambiguous case — the repro is unambiguous and repeatable. Per the plan's own
process, `build-regression-tests` should still write the red-state
regression test (RED with the defense stubbed out under *some* hypothetical
older Popoto, GREEN under installed 1.8.0) before any removal ships, but the
audit's empirical finding is that removal is *justified*, not merely
"ambiguous, default KEEP."

### `__setattr__` datetime coercion and `_normalize_kwargs` — KEEP (different mechanism, not disproven)

The `__getattribute__` finding above is scoped to the *missing-key* case. It
does **not** cover `__setattr__`'s coercion of *malformed values that are
actually present* (e.g. a `response_delivered_at` stored as an unparseable
ISO string, or an epoch float sneaking in via a non-`__init__` write path).
`decode_lazy_field` decodes msgpack bytes directly into `_decoded_fields`,
bypassing `__setattr__` entirely — so `__setattr__`'s coercion only fires for
explicit assignment (`session.foo = bar`), which happens via
`_normalize_kwargs` at construction and via any other code path that sets
these fields directly (e.g. hooks, session_health.py). This audit did not
attempt to disprove this mechanism (no repro showed it dead) — it remains
**KEEP**, and is a genuinely separate bug class (#929, malformed-value
coercion on write) from the lazy-load leak (#1099, missing-key read).

---

## Cluster B — status-index defenses (three sites)

### B1 — `_saved_field_values["status"]` backfill, `finalize_session`

`models/session_lifecycle.py:466-475`. Feeds Popoto's `IndexedFieldMixin`
the "old value" so the Lua script's `legacy_old_set` hint (`ARGV[6]`) can
locate and `SREM` the correct stale index Set **for rows that have not yet
been re-saved under 1.8.0 and therefore have no server-authoritative pointer**
(`popoto/fields/indexed_field_mixin.py:97`, `{field}\x00idxset`).

### B2 — `_saved_field_values["status"]` backfill, `transition_status`

`models/session_lifecycle.py:706-714`. Identical mechanism and identical
justification to B1, applied to the non-terminal transition path.

### B1/B2 verdict: KEEP, gated — shared disposition

Confirmed by reading `popoto/fields/indexed_field_mixin.py:60-124`
(`INDEX_SWAP_LUA`): the Lua script reads a server-authoritative pointer
(`HGET model_key ptr_field`) first; only when that pointer is absent does it
fall back to `ARGV[6]` (`legacy_old_set`, sourced from the client-supplied
`_saved_field_values`). Once every live row has been saved at least once
under 1.8.0 (establishing the pointer), the backfill becomes provably inert
— the Lua script's primary path (pointer-driven) no longer needs the hint.
**Until that migration runs, B1/B2 remain load-bearing for any row that
predates the pointer.** This audit did not find evidence that migration has
happened; PR #2081 landed today (2026-07-14) and no migration commit exists
in `scripts/update/migrations.py` referencing this pointer. **Verdict: KEEP,
gated on the conditional pointer-establishing migration (Task 3, only
authored if this verdict is REMOVE — see below).**

B1 and B2 **must** share one disposition — never remove one without the
other. Both feed the identical `_saved_field_values["status"]` mechanism for
the identical Lua fallback; removing only one leaves whichever transition
path (terminal vs. non-terminal) exercised through the surviving site's
absence stranding lazy-loaded legacy rows.

**Recommended default (per plan's ambiguity policy): KEEP both, author no
migration.** This audit did not attempt to determine what fraction of live
rows already carry a pointer (would require a Redis scan of `\x00idxset`
hash fields across all `AgentSession` keys, out of scope for a read-only
Task 1) — that measurement, if done, belongs to the migration-decision task
(Task 3), not this audit.

---

## B3 — defensive `srem`-across-`ALL_STATUSES`

`models/session_lifecycle.py:488-510`, inside `finalize_session` only (no
equivalent in `transition_status`, since only terminal transitions get the
blanket sweep). Originates from PR #954 (issue #950, merged 2026-04-14),
**four days after** PR #885 (2026-04-10) introduced the lifecycle CAS
authority (`get_authoritative_session` + `StatusConflictError`) that both
`finalize_session` and `transition_status` now perform *before* ever writing
a status. Reading PR #954's description: the actual root-cause fix for #950
was converting the other ~10 full-save call sites across
`agent_session_queue.py` and the hook layer to **partial saves**
(`save(update_fields=[...])`) so they stop writing `status` at all; the
defensive `srem` was added as a backstop for any full-save site that
survives that conversion (present or future).

### Finding 2 — the plan's Data Flow "bug class 1b" is correctly identified but its scope needs a correction

The plan's premise (Data Flow, bug class 1b / Race 1b) is that
`INDEX_SWAP_LUA`'s pointer atomicity guarantees index-consistency-with-value
but not value-freshness, so a stale full save from an external process
still clobbers `status` and B3 is needed to scrub the wrong Set afterward.
That premise is correct as far as it goes — but the empirical test below
shows the picture is more specific than "keep B3 unconditionally."

### Finding 3 — empirical #950 stale-object full-save red-state test (mandatory per plan Task 1(d))

Run against a live Redis instance (repro discarded from `/tmp` after the
run), in two variants — WITH B3's `srem` present (current code, by directly
exercising `finalize_session`) and WITHOUT it (monkey-patched
`POPOTO_REDIS_DB.srem` to a no-op for the duration of the run, simulating
B3's removal without touching committed code):

**Scenario: stale-object clobber immediately after a correct finalize.**
1. Process P2 loads a session (`status="running"`).
2. Process P1 loads the same row and calls `finalize_session(..., "completed")`.
   Sanity-checked: member lands exclusively in the `completed` index Set.
3. P2 (still holding the stale in-memory `status="running"`) does a full
   `session.save()`.
4. **Result — identical in both the WITH-B3 and WITHOUT-B3 runs:** the row
   is atomically moved to the `running` Set and atomically removed from the
   `completed` Set. It is **never present in more than one Set** — the
   clobbering full save goes through `INDEX_SWAP_LUA` like any other write,
   and the Lua script's pointer-driven `SREM`/`SADD` is unconditional. B3
   never gets a chance to run in this window anyway (it only executes
   *inside* `finalize_session`, and P2's stale save happens after
   `finalize_session` has already returned).

**Scenario: repair re-finalize after the clobber.**
5. A later repair path re-reads the (now-clobbered) row and calls
   `finalize_session(..., "completed")` again.
6. **Result — identical in both WITH-B3 and WITHOUT-B3 runs:** the row ends
   up exclusively in the `completed` Set, on-disk status `"completed"`. The
   repair succeeds via pointer atomicity alone; B3's blanket sweep makes no
   observable difference **because the pointer was already correctly
   updated to `running` by the clobbering write in step 3**, so the Lua
   script's primary (pointer-driven) path handles the repair's `SREM`
   correctly without needing B3 or the `_saved_field_values` hint at all.

**Conclusion: in the steady state (row has a pointer, i.e. any row saved at
least once since the 1.8.0 upgrade), B3 is empirically redundant** — every
write, clobbering or not, atomically lands the row in exactly one Set
matching its current pointer, and every subsequent legitimate write
atomically self-heals. This was tested twice (immediate post-clobber state,
and post-repair state) and both times B3's presence vs. absence produced
byte-identical Redis state.

**B3's real justification is not "value freshness" independent of
`INDEX_SWAP_LUA` — it is the same legacy-pointer gap as B1/B2.** B3's
`srem`-across-`ALL_STATUSES` does not consult `_saved_field_values` or the
pointer at all; it blindly sweeps every status Set. That blind sweep is only
*necessary* (as opposed to merely harmless) in the one scenario this audit
did not reproduce: a **legacy row with no pointer AND no correct
`legacy_old_set` hint** (e.g., a lazy-loaded object whose
`_saved_field_values` never got backfilled — B1/B2's job) undergoing a
clobbering write. In that compound scenario, `INDEX_SWAP_LUA` cannot
identify the true old Set by either the pointer or the hint, so it would
`SADD` the new Set without `SREM`-ing the true old one — genuinely
stranding the row in two Sets. B3's blind sweep is the only defense that
still works there, because it doesn't depend on knowing the old value at
all.

**Revised verdict: B3 is KEEP, but its disposition is entangled with the
same legacy-pointer gate as B1/B2, not an independently-justified
"value-freshness" defense as the plan's Data Flow section framed it.** This
is a correction worth carrying into the plan before any removal work
proceeds: if B1/B2 are ever verdicted REMOVE (post-migration, all rows
carry a pointer), B3 should be re-evaluated *at the same time*, not treated
as permanently un-removable. Conversely, if B1/B2 stay KEEP indefinitely
(the plan's stated likely default), B3 should stay KEEP for the identical
reason, not a separate one.

This finding does **not** authorize removing B3 now — the compound
legacy-no-pointer scenario was not reproduced (doing so requires simulating
a genuinely pointer-less row, which requires either a pre-1.8.0 Redis
snapshot or manually stripping the `status\x00idxset` hash field _and_
leaving `_saved_field_values` empty; out of scope for this pass, flagged
below as an Open Question). Per the plan's "ambiguity defaults to KEEP"
policy, and because the untested compound scenario is exactly the one B3
exists for, **B3 verdict: KEEP**, with the reasoning above superseding the
plan's original "value-freshness" framing.

---

## Cluster C — out of scope, confirm-and-keep

| Symbol | File:Line | Bug class |
|---|---|---|
| `_heal_future_updated_at` | `models/agent_session.py:977-1033` | Clock skew (#1645/#1817) — detection only, read-only classmethod |
| `save()` `updated_at` UTC stamp override | `models/agent_session.py:945-974` | Same, write-side |
| `repair_indexes` | `models/agent_session.py:2102-2144` | `rebuild_indexes()` class-set delete/re-add window (#1720) — a different concern from the save-race |
| `cleanup_expired` | `models/agent_session.py:2146+` | TTL-based row cleanup, unrelated to index races |

All four confirmed present at the cited lines by grep; none reference
`_saved_field_values`, `INDEX_SWAP_LUA`, or the status index. No empirical
work was done here — this matches the plan's No-Gos ("confirm-and-keep only,
do not modify"). `repair_indexes()`'s existing `(stale_count, rebuilt_count)`
return value is what a future active stale-count detector (Task 3b, if any
Cluster B defense is removed) would build on — confirmed present at
`models/agent_session.py:2102`, unchanged by this audit.

---

## Migration requirement

**Not determined by this audit.** Per plan Technical Approach: the
pointer-establishing migration is authored only if the B1/B2 verdict is
REMOVE. This audit's default recommendation is KEEP-all (see verdicts
table), which per the plan means **no migration is required and none should
be authored** at this time. If a future task chooses to pursue B1/B2
removal, it must first measure what fraction of live `AgentSession` rows
already carry a `status\x00idxset` pointer (a Redis scan, not done in this
audit) to decide whether the migration is even necessary in practice.

## Open Questions (default: KEEP)

1. **Compound legacy-no-pointer + no-hint scenario for B3** — not
   reproduced in this audit (would require simulating a genuinely
   pointer-less row with an also-empty `_saved_field_values`). This is the
   one scenario where B3's blind sweep is not just harmless but load-bearing.
   Default: KEEP B3 until this is reproduced and shown to still strand the
   row without B3, or shown to be structurally impossible in the current
   codebase (e.g., if every live-row read path always populates
   `_saved_field_values` correctly, making the "no hint" half of the
   compound scenario unreachable).
2. **Fraction of live rows with an established pointer** — not measured.
   Needed before any B1/B2 removal proceeds; would determine whether the
   conditional migration (Task 3) is actually necessary or whether organic
   traffic since PR #2081 (merged today) has already re-saved every live row.
3. **Cluster A's `__setattr__`/`_normalize_kwargs` scope** — the plan's
   Open Question 3 asked whether any Cluster A defense beyond the
   `__getattribute__` missing-field case is worth a removal attempt. This
   audit did not disprove `__setattr__`'s malformed-value coercion or
   `_normalize_kwargs`'s `response_delivered_at` handling (#929) — they
   guard a materially different mechanism (explicit-write coercion, not
   lazy-load default-fill) and remain KEEP by default.
4. **Sentry ledger granularity** (plan's Open Question 2) — unresolved by
   this audit; a documentation/build-task decision, not an audit finding.

## Reasoning tied to `INDEX_SWAP_LUA`'s actual guarantee

`INDEX_SWAP_LUA` (`popoto/fields/indexed_field_mixin.py:60-124`) guarantees:
1. **Atomicity**: the read-pointer → SREM-old → SADD-new → write-pointer →
   HSET-value sequence is a single Redis-server-side Lua execution — no
   client-side interleave window exists for any write that goes through it.
2. **Scope**: only fields whose class mixes in `IndexedFieldMixin`
   (`IndexedField`, `UniqueField`, `KeyField` subclasses) — confirmed via
   `models/base.py:1131,1292` ("Exclude IndexedFieldMixin fields — EVAL
   (INDEX_SWAP_LUA) owns their maintenance" from the plain-HSET path). On
   `AgentSession`, only `status`, `task_type`, `claude_session_uuid`, and
   `claude_pid` are `IndexedField`; every field in Cluster A's defenses
   (`exit_returncode`, `tool_timeout_count_*`, `response_delivered_at`,
   `last_heartbeat_at`, etc.) is a plain `Field`/`IntField`/`DatetimeField`,
   entirely outside INDEX_SWAP_LUA's scope. This confirms spike-1's premise
   that Cluster A and Cluster B are genuinely different bug classes, even
   though this audit revises Cluster A's specific verdict.
3. **Pointer-dependence**: correctness of the atomic SREM depends on a
   server-authoritative pointer already existing in the hash. For rows
   without one, the Lua script falls back to a client-supplied hint
   (`legacy_old_set`) — this is the sole channel through which B1/B2's
   backfill (and, transitively, B3's continued necessity) remains relevant.
4. **What it does NOT guarantee**: that the *value* being written is fresh
   (a stale full save from a process holding an outdated in-memory object
   still writes that stale value, atomically). This audit found that this
   gap is real but narrower than the plan assumed: it is fully absorbed by
   the existing CAS guards in `finalize_session`/`transition_status`
   (`get_authoritative_session` + `StatusConflictError`, added by PR #885,
   *before* #950) for any write that goes through those two lifecycle
   functions. It remains a live gap only for `session.save()` calls that
   bypass both lifecycle functions entirely (the original #950 vector,
   substantially closed by PR #954's partial-save conversions elsewhere in
   the codebase) and, secondarily, for the legacy-pointer compound case
   B3 guards.
