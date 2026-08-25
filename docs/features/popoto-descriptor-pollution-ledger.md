# Popoto Descriptor-Pollution and Index-Race Defenses

This is the inventory of descriptor-pollution and index-race compensators in
`models/agent_session.py` and `models/session_lifecycle.py`, grounded in what
Popoto 1.8.0's `INDEX_SWAP_LUA` actually guarantees.

## TL;DR verdicts

| Cluster | Defense | File:Line | Verdict |
|---|---|---|---|
| A | `__getattribute__` missing-field descriptor substitution | `models/agent_session.py` | **ABSENT** — Popoto ≥1.6.1 default-fills absent fields at construction, so the substitution is dead; see Finding 1 |
| A | `_INT_FIELDS_BACKCOMPAT` set | `models/agent_session.py:636` | KEEP (consumed by `__setattr__`'s write-path coercion) |
| A | `_DATETIME_FIELDS` + `__setattr__` datetime coercion | `models/agent_session.py:618`, `:697` | KEEP (guards malformed *values* on explicit write) |
| A | `_normalize_kwargs` `response_delivered_at` coercion | `models/agent_session.py:743` | KEEP (guards malformed values arriving via `__init__`) |
| B1 | `_saved_field_values["status"]` backfill, site 1/2 | `models/session_lifecycle.py` (`finalize_session`) | **KEEP** — load-bearing for pointer-less rows (Finding 2) |
| B2 | `_saved_field_values["status"]` backfill, site 2/2 | `models/session_lifecycle.py` (`transition_status`) | **KEEP** — same mechanism/evidence as B1 |
| B3 | Defensive `srem`-across-`ALL_STATUSES` | `models/session_lifecycle.py` | **KEEP** — load-bearing in the compound legacy scenario (Finding 4) |
| C | `_heal_future_updated_at`, `save()` updated_at stamp, `repair_indexes`, `cleanup_expired` | `models/agent_session.py` | OUT OF SCOPE — different bug classes (clock skew / rebuild-window / TTL), confirm-and-keep |

B1 and B2 share one disposition — never remove one without the other (enforced by the structural guard test `test_both_backfill_sites_move_together`).

---

## INDEX_SWAP_LUA's actual guarantees

`INDEX_SWAP_LUA` (`popoto/fields/indexed_field_mixin.py` in the installed
1.8.0 dist) guarantees:

1. **Atomicity**: read-pointer → SREM-old → SADD-new → write-pointer →
   HSET-value is a single server-side Lua execution — no client-side
   interleave window for writes that go through it.
2. **Scope**: only fields mixing in `IndexedFieldMixin`. Confirmed at
   `popoto/models/base.py` ("Exclude IndexedFieldMixin fields — EVAL
   (INDEX_SWAP_LUA) owns their maintenance" on the plain-HSET path). On
   `AgentSession` there are THREE `IndexedField`s — `status`, `task_type`,
   `claude_session_uuid`. (The execution pid is a plain, non-indexed
   `exec_pid` field — see [AgentSession Fenced Execution Record](agent-session-fenced-execution-record.md).)
   `status` is the only IndexedField any Cluster B defense concerns. Every
   field in Cluster A's defenses (`exit_returncode`, `tool_timeout_count_*`,
   `response_delivered_at`, `last_heartbeat_at`, etc.) is a plain
   `Field`/`IntField`/`DatetimeField` — entirely outside INDEX_SWAP_LUA's
   scope.
3. **Pointer-dependence**: the atomic SREM of the old Set requires the
   server-authoritative `{field}\x00idxset` pointer in the model hash. For
   rows without one, the Lua falls back to the client-supplied
   `legacy_old_set` hint (`ARGV[6]`, sourced from `_saved_field_values`) —
   this is the sole channel through which B1/B2's backfill remains
   load-bearing.
4. **What it does NOT guarantee**: freshness of the *value* being written. A
   stale full save writes a stale value atomically, and the index follows
   the (stale) value. Pointer atomicity ≠ value freshness.

## Cluster A — plain, non-indexed Fields

### Finding 1 — the missing-field descriptor leak does not reproduce under Popoto 1.8.0

Popoto's `_create_lazy_model` ("Initialize defaults for fields absent from
the hash" loop) default-fills every field absent from the Redis hash at
construction time, before any attribute access. Reads of absent IntField /
DatetimeField values return scalars (`0` / `None`) both through
`AgentSession.__getattribute__` and through `popoto.models.base.Model.__getattribute__`
directly — never a descriptor object.

The `__getattribute__` override is therefore absent: its sole job was
substituting the descriptor object on missing-field reads, a case Popoto
handles itself. The committed regression test
(`tests/unit/test_agent_session.py::TestClusterARemoveCandidateEmpiricalRegression`)
reproduces the original missing-field scenario and stays green with the
override gone; a future Popoto regression in the default-fill turns that
test red.

`_INT_FIELDS_BACKCOMPAT` remains — `__setattr__` still consumes it to coerce
malformed values written explicitly (bad type → 0).

### `__setattr__` coercion and `_normalize_kwargs` — KEEP

Finding 1 is scoped to the *missing-key read* case. It does not cover
coercion of malformed values that are actually present or explicitly
assigned (e.g. `response_delivered_at` as an unparseable ISO string, an
epoch float via a non-`__init__` write path). `decode_lazy_field` decodes
msgpack directly into `_decoded_fields` (bypassing `__setattr__`), so
`__setattr__`'s coercion fires only for explicit assignment — which happens
at construction via `_normalize_kwargs` and in hooks/session_health write
paths. These guards a materially different mechanism than the missing-field
read arm and are kept.

## Cluster B — status-index defenses (three sites)

### Finding 2 — B1/B2 backfill is load-bearing for pointer-less rows

For a row stripped of the `status\x00idxset` pointer (a pre-1.8.0 row),
transitioning `pending → running` via a plain `save()`:

- **WITHOUT the backfill:** row stranded in BOTH `pending` and `running`
  index Sets (Lua has no pointer and an empty hint, so it SADDs without
  SREMing).
- **WITH the backfill** (`_saved_field_values["status"] = "pending"`): clean
  swap — row in `running` only.

B1/B2 remain the only source of the `legacy_old_set` hint for rows that
predate the 1.8.0 pointer.

### Finding 3 — steady-state stale-object full save: B3 makes no observable difference

In the steady state (row has a pointer), pointer atomicity fully absorbs a
clobber-then-repair sequence: the value can go stale, but the index always
matches the value, and any subsequent legitimate write self-heals.

### Finding 4 — B3 is load-bearing in the compound legacy scenario

For a legacy row (pointer stripped) already stranded in `{pending, running}`
by a stranding write, a `finalize_session(..., "completed")` on a fresh read:

- **WITH B3:** row ends exclusively in `completed` — the blind sweep scrubs
  the stray `pending` membership.
- **WITHOUT B3:** row ends in `{completed, pending}` — still stranded. The
  B1 backfill's hint only SREMs the *current on-disk* status Set; neither the
  pointer nor the hint knows about the stray `pending` membership. Only B3's
  blind sweep repairs it.

B3 is the only repair path for pre-existing strandings (legacy rows, crashed
mid-write rows, any historical index drift) at the moment a session reaches a
terminal state. If B1/B2 are ever removed, B3 must be re-evaluated at the
same time, not independently.

The committed red-state test
(`tests/unit/test_agent_session_index_corruption.py::TestStaleFullSaveRedState950`)
pins both findings: the steady-state arm (no stranding, WITH and WITHOUT B3)
and the compound-legacy arm (stranded WITHOUT B3, clean WITH it).

## Cluster C — out of scope, confirm-and-keep

| Symbol | File:Line | Bug class |
|---|---|---|
| `_heal_future_updated_at` | `models/agent_session.py:985` | Clock skew — detection only |
| `save()` `updated_at` UTC stamp override | `models/agent_session.py:953` | Same, write-side |
| `repair_indexes` | `models/agent_session.py:2110` | `rebuild_indexes()` class-set delete/re-add window; drift commits touch only this Cluster-C site |
| `cleanup_expired` | `models/agent_session.py:2237` | TTL-based row cleanup, unrelated |

None reference `_saved_field_values`, `INDEX_SWAP_LUA`, or the status index
maintenance path.

## Removed-defenses Sentry ledger (passive regression tripwire)

| Removed defense | Sentry tripwire | Signature |
|---|---|---|
| `AgentSession.__getattribute__` missing-field descriptor substitution | [VALOR-E1](https://yudame.sentry.io/issues/7604718329/) (dup: [VALOR-D0](https://yudame.sentry.io/issues/7599482038/)) | `TypeError: '<=' not supported between instances of 'str' and 'int'` in `_agent_session_tool_timeout_check` (`agent/session_health.py`) |

The removed defense's failure mode, if it regressed, is a **type-comparison
error in the health-check read path**: a non-scalar (descriptor object) or
mistyped value reaching `exit_returncode` / `tool_timeout_count_*` where the
OOM / tool-timeout detectors do `int` comparisons. VALOR-E1 is the exact
signature that family produces. Generic Popoto field/descriptor leakage of
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

1. **Fraction of live rows with an established pointer** — unmeasured; only
   relevant if a future task pursues B1/B2 removal.
2. **Cluster A `__setattr__`/`_normalize_kwargs` scope** — they guard
   explicit-write value coercion, a materially different mechanism from the
   missing-field read arm. KEEP.
