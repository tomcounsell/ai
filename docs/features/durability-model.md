# Durability Model: Room / Job / AgentSession

> **Naming collision:** the web dashboard (`localhost:8500/`) also has a
> top-level list it calls "Jobs" (`ui/data/jobs.py::JobGroup`). That is an
> unpersisted, render-time grouping of `AgentSession` runs — not this `Job`.
> The two share no identifier. The dashboard's session detail modal resolves
> and shows *this* `Job` separately, only for the one session open in the
> modal; see [Dashboard §Jobs Table](dashboard.md#jobs-table) and
> [§Modal Job block](dashboard.md#modal-job-block).

The single place that answers **"a message or an obligation is durable because X."**
Shipped incrementally by `docs/plans/durability-room-job-agentrun.md`
(issue #2494): Milestone 1 (fenced execution record), Milestone 2 (Room +
durable inbox, PR #2622), Milestone 3 (Job + routing + advisory gating);
generalized by #2708 (expectations as the single obligation primitive).

## The three-model shape

| Model | What it is | Durability property |
|-------|-----------|---------------------|
| **`Room`** (`models/room.py`) | The environment a conversation happens in: `(project_key, addressee)`, addressee ∈ `telegram:{chat_id}` \| `email:{address}` \| `system`. Resolved from `projects.json` (config stays the source of truth). | **Immortal** (no TTL). Owns the durable inbox — a message appended to a Room cannot be addressed to a dead object. DMs and groups are covered identically. |
| **`Job`** (`models/job.py`) | A responsibility to complete something end to end. Carries a required, **append-only-versioned `goal`** and every recorded **expectation** as appended/discharged entries on it. | **Never hard-closed, no TTL.** Goes to rest by age (`status="at-rest"`) — but never while an expectation is open; *any* new message revives it regardless of age. The Job outlives every session that served it. |
| **`AgentSession`** (`models/agent_session.py`) | An agent's context: resume handle, parent hierarchy, and the **fenced execution record** (`harness`, `exec_pid`, `pid_create_time`, cwd, spawn history). There is no separate run model. | Bounded by `Meta.ttl`. Crash-resume appends a new fence record; the newest entry is the live fence recovery paths read. See [`agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md). |

## Inbound flow (target ordering)

👀 (*bridge saw it*) → **durable Room-inbox append** → route → bind + reaction.

The append (`Room.append_inbox`, written by `bridge/room_inbox.py`) collapses
the intake loss window to a single Redis write. Cutover is 3-phase; phase 1
(shadow — append written alongside the untouched dispatch flow) shipped in
PR #2622, and Milestone 3 added shadow bind-or-mint Job routing at the same
seam. Shadow coverage spans every **Telegram** enqueue path, live intake and recovery
alike: the three recovery re-enqueue scanners (`bridge/catchup.py`,
`bridge/reconciler.py`, `bridge/agent_catchup.py`) append alongside their
untouched enqueue with the same entry shape (including `strip_private` on the
persisted text), because recovery and live intake are one code path — the
2026-08-18 phase-2 gate run caught re-enqueued messages bypassing the inbox.
Email intake is **not** covered: `bridge/email_bridge.py` enqueues inbound user
email with no Room-inbox append, so an email-originated message has no inbox
entry in shadow. Dispatch still runs the legacy session path until the
authoritative flip ships as its own release.

## Job routing (`bridge/job_router.py`)

The **single routing authority** for inbound messages — it replaced the
expectations-based semantic session router, now deleted. Session-level routing
is now purely mechanical: reply-to resumes a session, everything else is a
fresh session.

- **Reply-to** routes through the **permanent reply index** —
  `reply:{chat_id}:{message_id}` → `{job_id, room_id}`, a standalone
  non-Popoto string key bound via `SET NX`, **no TTL** — with no model call.
  Written for inbound messages at routing time and for **outbound** messages
  at the relay's send-success site, so a user reply to Valor's own message
  lands on the same Job.
- **Bind-or-mint** runs on the **local granite model via PydanticAI**
  (`agent.llm.run_typed_local`, strict `JobRouteDecision` JSON output), as
  does the intake intent classifier (`tools/classifier.py`). Structure:
  zero-candidate short-circuit, top-5 recency cap (`Job.recent_for_room`,
  §Bounded recency read below), post-hoc `valid_ids` membership check,
  confidence threshold (`JOB_ROUTER_CONFIDENCE_THRESHOLD`, provisional
  0.70).
- **Total fail-open to NEW**: any model/transport failure mints an extra Job
  — never a lost or wrong-bound message. The durable append precedes
  routing, so router latency/failure is a UX cost, not a durability cost.
  Over-mint (~35% measured in spike-3) is the one production number to
  watch; the reduction lever is prompt tuning, not the threshold.
- Binding is **idempotent** on the message identity (`SET NX`): a
  crash-after-bind re-route finds the existing binding and no-ops (Race 4).

### Bounded recency read (`Job.recent_for_room`)

`recent_for_room` answers the top-N bind-or-mint candidate lookup with a
direct bounded reverse-range read over the `last_active_at` SortedField's
per-Room partition, cost `O(limit)` regardless of the Room's lifetime Job
count: the partition key is derived (never hand-built — `DB_key.clean()`
escapes `:`, and every real `room_id` contains one), a single `ZREVRANGE`
pulls the top `limit + JOB_RECENT_OVERFETCH` members (`JOB_RECENT_OVERFETCH`,
env-overridable, default 5), and `Job.query.get_many(..., skip_none=True)`
hydrates only those members in one pipelined round trip. The over-fetch
absorbs a member whose backing hash is already gone; an under-filled result
is never re-fetched — the Room genuinely has fewer live Jobs, or an index
repair is mid-flight. Any failure logs a warning and fails open to `[]`,
which the router treats as "no candidates" and mints.

### `last_active_at` score purity (`Job.save()`)

The sorted-set score behind `recent_for_room` must be a pure UTC epoch.
popoto decodes a stored datetime without tzinfo, so a reloaded Job carries a
naive `last_active_at`; `Job.save()` re-attaches UTC to a naive value before
every write funnels into popoto's own scoring, making every score correct by
construction. This is **instant-preserving, not a re-stamp** — it never
assigns "now", only tzinfo, so an unrelated save (an expectation write, a
goal version) cannot resurrect an idle Job's recency. The reattach is scoped
to when `last_active_at` is actually being written: a save whose
`update_fields` excludes it (`Job.backfill_open_expectations_index`'s
`has_open_expectations`-only write) leaves the field, and the score, alone.

Skew accumulated by any Job re-saved before this override shipped is swept
by the `backfill_job_last_active_scores` migration
(`scripts/update/migrations.py`), which runs on every machine's `/update`.
For each Job it compares the stored sorted-set score against the UTC epoch
the hash value implies; a row outside a 1-second tolerance is re-read fresh
and repaired with a field-scoped `save(update_fields=["last_active_at"])` —
the same clobber-proof idiom as `backfill_open_expectations_index`, so a
concurrent expectation write is never overwritten. The migration is
idempotent (a repaired row is in-tolerance on the next pass, so a re-run
costs reads only) and fleet-convergent (every machine shares the same Redis,
so re-running it anywhere converges scores written by a peer still on
pre-override code). The sweep's single implementation is
`Job.renormalize_last_active_scores()`, which `Job.repair_indexes()` also
runs after every index rebuild — popoto's `rebuild_indexes()` re-scores
naive-decoded instances in local time, bypassing the `save()` reattach, so
the repair path re-normalizes what the rebuild would otherwise re-skew on a
non-UTC host.

## Goals and expectations (the single obligation primitive)

The router is deliberately not smart enough to author a goal: at mint the
Job's goal is the mechanical placeholder `handle user message '<first 20
chars>…'`. **Authoring the real goal (v1) is the PM's mandated first step**
(enforced in `prime-pm-role` priming, nudged by the outbound advisory pass),
via `python -m tools.job_tool author-goal`. Goal versions are append-only —
history is never overwritten.

**Expectations are the single obligation primitive** — one entry shape
`(holder, owner, what, direction)` on the Job's goal JSON, covering both
directions (#2708):

- **`inbound`** — what we owe a requester (holder = the requester,
  owner = the PM). Subsumes the retired promise model.
- **`outbound`** — what a spawned lane owes its PM (holder = the PM
  session, owner = the lane session id/slug). Recorded at the spawn
  chokepoint (`tools/valor_session.py` create core) as a **null-fallback**:
  PM-authored entries are canonical (`--expect-what`, or
  `job_tool expectation-add`), and when none exists for the new lane the
  core stamps a mechanical entry marked `placeholder` (provenance-derived,
  mirroring `goal_is_placeholder()`) so the obligation is never
  unrecorded. The fallback trigger is **per lane** — a second lane is
  never skipped because the first one is covered.

Rules, in flow order:

1. The drafter promise gate (see [`promise-gate.md`](promise-gate.md)) stays
   the detection chokepoint and stays **advisory** — no verdict ever writes
   an obligation. On a deferral-shaped outbound the blocked draft carries a
   revise-or-override suggestion
   (`bridge/promise_gate.build_promise_advisory`, strictly read-only) back
   to the PM through the self-draft steering path.
2. **Revise**: the PM rewrites to claim only delivered work.
   **Override**: the PM stands by the obligation by recording it —
   `tools/job_tool expectation-add --direction inbound` — and resends. A
   recorded **open inbound** expectation clears the gate
   (`promise_recorded_override`); outbound expectations never do (a lane's
   obligation to the PM says nothing about what we owe the requester). The
   override is **Job-scoped by design**: any open inbound expectation on
   the bound Job clears the gate for every outbound on that Job until
   discharge.
3. **Discharge is owner-authored, always**: `expectation-remove` stamps
   `removed_ts` (append-only; the entry is kept). Nothing mechanical ever
   discharges — the reconciler surfaces evidence; the PM decides.
4. **Status and rest derive from the chokepoint**: `Job._write_goal_data`
   is the single write site that derives `has_open_expectations` AND forces
   `status="active"` while any expectation is open, so the two can never
   disagree. `Job.sweep_to_rest()` (called from the `agent/session_health.py`
   periodic sweep) transitions active Jobs idle past
   `JOB_AT_REST_AGE_SECONDS` (provisional 72h, env-overridable) to
   `at-rest` — **skipping any Job with an open expectation**. "Is this Job
   finished?" is answerable from state: a Job with an open expectation
   cannot be at rest. An expectation-less idle Job still rests by age, so
   under-recording degrades to today's behavior, never to a false "done".
5. **Invariant alarm**: the renamed
   `_check_jobs_at_rest_with_open_expectations` health check (still the
   sole `sweep_to_rest()` caller) intersects the `status` and
   `has_open_expectations` indexes — empty in steady state by
   construction; any hit is drift or a migration edge, surfaced to the
   operator log only.
6. **Drift advisory**: on the same cadence, a live PM whose live eng
   children are not covered by a matching open outbound expectation on its
   bound Job is surfaced to the operator log (advisory only, no writes) —
   the backstop for spawn paths where no Job resolves.
7. **Reconciler**: open outbound expectations whose owners are gone are
   recovered by `reflections/expectation_reconciler.py` — see
   [`expectation-reconciler.md`](expectation-reconciler.md).
8. **A corrupt goal fails closed on every write** (#2862). The `goal` bytes
   are the only copy of a Job's obligation history, so two categories of
   bad shape get two handlers. A null field or a wrong-shaped JSON value is
   something our own writer can leave behind: `_goal_data()` coerces it to
   empty and it stays writable. Bytes that do not decode at all (or a
   non-string value) are corruption: `Job.goal_is_corrupt()` is true, reads
   still answer empty so unrelated callers keep working, but every read
   logs at ERROR and sends one Sentry event per process per Job, and every
   mutator (`add_expectation`, `discharge_expectation`,
   `append_goal_version`, and the `_write_goal_data` chokepoint itself)
   raises `CorruptGoalError` instead of persisting `{"versions": [],
   "expectations": []}` over the original bytes. The Job is **pinned
   visible** until a human repairs it: `sweep_to_rest` never rests it, the
   daily `has_open_expectations` backfill never re-derives its flag from
   the empty parse, `with_open_expectations()` /
   `at_rest_with_open_expectations()` retain it (the stored flag is the
   last known truth and an empty parse cannot disprove it), the reconciler
   reports it as a `corrupt-goal: <job_id>` finding, the at-rest alarm
   names the corruption, and `job_tool show` exposes `goal_corrupt: true`.

`tools/job_tool.py` enforces **Room scope at the tool layer**: every Job
lookup filters on the calling session's own `room_id`, so cross-Room Jobs
are structurally unaddressable (prompt-level constraints drift; tool-level
ones don't).

## Private-tag stripping happens at intake

`<private>...</private>` is the user's inline opt-out from durable storage
(`agent/private_tag.py`; the memory-side contract lives in
[`subconscious-memory.md`](subconscious-memory.md)). The ruling for where
`strip_private` applies is the simple one: **every path that reads inbound
message text strips it at the point of first read, before anything is
logged or persisted.** Live and recovery are the same rule; "recovery" is
never a reason to hold raw text longer.

| Path | Strip point |
|------|-------------|
| Live intake (`bridge/telegram_bridge.py`) | `safe_text = strip_private(text)` right after `message.text` is read; `safe_text` feeds bridge.log, Memory, `TelegramMessage`, and `AgentSession.message_text` via `safe_clean_text` |
| Startup catchup (`bridge/catchup.py`) | `text = strip_private(message.text or "")` at the top of the per-message loop |
| Periodic reconciler (`bridge/reconciler.py`) | same, at the top of the per-message loop |
| Agent-judgment catchup (`bridge/agent_catchup.py`) | `read_thread` strips when it builds each `ThreadMessage`, so the judge transcript, the recovery log line, and the enqueued `message_text` all descend from one stripped read |
| Reply-chain prehydration | `format_reply_chain` output is stripped before it is spliced into `message_text` (both hydration sites) |

Stripping at intake means the two durable stores a recovery pass touches,
`bridge.log` (the recovery log line) and `AgentSession.message_text` (the
enqueue call), can only ever see stripped text; there is no later boundary
to remember. `tests/unit/test_recovery_strip_private.py` drives each scanner
with a private-tagged payload and asserts the span reaches neither store.

## Reactions are durable

A sent reaction is recorded as a reply-to message in the existing message
log with escaped, parseable content — `<reaction>👀</reaction>`,
`message_type="reaction"`, `direction="out"` — at the relay's send-success
site (`bridge/telegram_relay.py::_record_sent_reaction`). No new model, no
new subsystem.

## User-visible behavior: revival after apparent completion

**A Job can resume long after it looked done.** Any reply to a message in
its thread — regardless of age — revives an at-rest Job (`Job.revive()`).
This is new, deliberate behavior, not an internal resume-handle detail: a
seemingly-closed conversation can pick up where it left off weeks later.

## Index safety (#2207 discipline)

Both `Room` and `Job` ship with their own guarded `repair_indexes()`, are
registered in `_run_guarded_repairs()` so that repair path actually runs, and
carry a `_GUARDED_ELSEWHERE` entry in `scripts/popoto_index_cleanup.py` so the
generic `rebuild_indexes()` sweep skips them instead; both register a
`ModelDriftSpec` in `agent/index_drift.py` (drift detection never silently
narrows). Listing a model in `_GUARDED_ELSEWHERE` without registering it in
`_run_guarded_repairs()` leaves it with no index hygiene at all — the gap
that produced #2640. `Job` carries two IndexedFields, both low-cardinality:
`status` (active/at-rest) and the derived boolean `has_open_expectations`
(Schema Gate Amendment 2, #2708); no index holds a pid, uuid, or timestamp. The
reply index is a plain string KV — no hash, no class set, no secondary index —
so the identity-less-hash flood mechanism structurally cannot occur for it.

The daily `has_open_expectations` backfill
(`Job.backfill_open_expectations_index()`, run from `repair_indexes()` while
the bridge and workers are live) writes *only* that field via
`save(update_fields=["has_open_expectations"])`. Popoto
excludes IndexedFields from the plain HSET mapping and maintains them through
an atomic Lua EVAL instead, so a save whose entire field list is IndexedFields
sends no `goal` bytes at all — a maintenance pass on this path can never
overwrite a concurrently-written `goal` (#2647).

The same clobber-proof idiom governs the lifecycle transitions themselves:
`Job.touch()`, `Job.mark_at_rest()`, and `Job.revive()` each save only the
fields they mutate (`["last_active_at"]`, `["status"]`, and
`["status", "last_active_at"]` respectively), so an in-flight expectation
write on the same Job is never clobbered by a routing decision or a rest
sweep (#2860). `mark_at_rest()` deliberately omits `last_active_at` from its
`update_fields` — resting a Job by age never refreshes its recency. A side
effect of scoping these three writes is that they no longer incidentally
re-run `on_save` for `has_open_expectations` (or, for `touch`/`revive`,
`last_active_at`) the way a full-hash save did. Index self-heal for those
fields is owned exclusively by the two sanctioned sweeps —
`backfill_open_expectations_index()` and `renormalize_last_active_scores()`
— not by any lifecycle transition.
