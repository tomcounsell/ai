# Durability Model: Room / Job / AgentSession

The single place that answers **"a message or a promise is durable because X."**
Shipped incrementally by `docs/plans/durability-room-job-agentrun.md`
(issue #2494): Milestone 1 (fenced execution record), Milestone 2 (Room +
durable inbox, PR #2622), Milestone 3 (Job + routing + advisory promises).

## The three-model shape

| Model | What it is | Durability property |
|-------|-----------|---------------------|
| **`Room`** (`models/room.py`) | The environment a conversation happens in: `(project_key, addressee)`, addressee ∈ `telegram:{chat_id}` \| `email:{address}` \| `system`. Resolved from `projects.json` (config stays the source of truth). | **Immortal** (no TTL). Owns the durable inbox — a message appended to a Room cannot be addressed to a dead object. DMs and groups are covered identically. |
| **`Job`** (`models/job.py`) | A responsibility to complete something end to end. Carries a required, **append-only-versioned `goal`** and the PM's promises as appended/removed entries on it. | **Never hard-closed, no TTL.** Goes to rest by age (`status="at-rest"`); *any* new message revives it regardless of age. The Job outlives every session that served it. |
| **`AgentSession`** (`models/agent_session.py`) | An agent's context: resume handle, parent hierarchy, and the **fenced execution record** (`harness`, `exec_pid`, `pid_create_time`, cwd, spawn history). There is no separate run model. | Bounded by `Meta.ttl`. Crash-resume appends a new fence record; the newest entry is the live fence recovery paths read. See [`agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md). |

## Inbound flow (target ordering)

👀 (*bridge saw it*) → **durable Room-inbox append** → route → bind + reaction.

The append (`Room.append_inbox`, written by `bridge/room_inbox.py`) collapses
the intake loss window to a single Redis write. Cutover is 3-phase; phase 1
(shadow — append written alongside the untouched dispatch flow) shipped in
PR #2622, and Milestone 3 added shadow bind-or-mint Job routing at the same
seam. Dispatch still runs the legacy session path until the authoritative
flip ships as its own release.

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
  zero-candidate short-circuit, top-5 recency cap
  (`Job.recent_for_room`, a `SortedField(partition_by=room_id)`), post-hoc
  `valid_ids` membership check, confidence threshold
  (`JOB_ROUTER_CONFIDENCE_THRESHOLD`, provisional 0.70).
- **Total fail-open to NEW**: any model/transport failure mints an extra Job
  — never a lost or wrong-bound message. The durable append precedes
  routing, so router latency/failure is a UX cost, not a durability cost.
  Over-mint (~35% measured in spike-3) is the one production number to
  watch; the reduction lever is prompt tuning, not the threshold.
- Binding is **idempotent** on the message identity (`SET NX`): a
  crash-after-bind re-route finds the existing binding and no-ops (Race 4).

## Goals and promises (PM-authored, advisory-gated)

The router is deliberately not smart enough to author a goal: at mint the
Job's goal is the mechanical placeholder `handle user message '<first 20
chars>…'`. **Authoring the real goal (v1) is the PM's mandated first step**
(enforced in `prime-pm-role` priming, nudged by the outbound advisory pass),
via `python -m tools.job_tool author-goal`. Goal versions are append-only —
history is never overwritten.

**Promises have no obligation model and no mechanical trigger** (Risk 4:
every mechanical design either under- or over-fires). Instead:

1. The drafter promise gate (see [`promise-gate.md`](promise-gate.md)) stays
   the detection chokepoint. On a deferral-shaped outbound it is
   **advisory**: the blocked draft carries a revise-or-override suggestion
   (`bridge/promise_gate.build_promise_advisory`, strictly read-only) back
   to the PM through the self-draft steering path.
2. **Revise**: the PM rewrites to claim only delivered work.
   **Override**: the PM stands by the promise by recording it —
   `tools/job_tool promise-add` appends it to the Job's goal record — and
   resends. A recorded **open** promise clears the gate
   (`promise_recorded_override`). The override is **Job-scoped by design**:
   any open promise on the bound Job clears the gate for every outbound on
   that Job until discharge — once the PM has durably stood by a promise,
   re-blocking each subsequent deferral on the same Job would be the nag
   machine Risk 4 forbids. This is the designed semantic per the plan's
   advisory framing, not per-message matching.
3. **Discharge is PM-authored too**: `promise-remove` stamps `removed_ts`
   (append-only; the entry is kept).
4. **At-rest backstop**: the `agent/session_health.py` periodic sweep first
   applies **rest-by-age** — `Job.sweep_to_rest()` transitions active Jobs
   idle past `JOB_AT_REST_AGE_SECONDS` (provisional 72h, env-overridable)
   to `at-rest` via the ORM — then surfaces Jobs at rest with open
   promises to the **operator log only** (never human chat), alongside the
   operator metric `metrics:promise_advisories_issued` vs
   `metrics:promises_authored` — an ignored advisory is visible, not
   silent.

`tools/job_tool.py` enforces **Room scope at the tool layer**: every Job
lookup filters on the calling session's own `room_id`, so cross-Room Jobs
are structurally unaddressable (prompt-level constraints drift; tool-level
ones don't).

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

Both `Room` and `Job` ship with their own guarded `repair_indexes()` and a
`_GUARDED_ELSEWHERE` entry in `scripts/popoto_index_cleanup.py`, so the
generic `rebuild_indexes()` sweep never touches them; both register a
`ModelDriftSpec` in `agent/index_drift.py` (drift detection never silently
narrows). `Job` carries two IndexedFields, both low-cardinality: `status`
(active/at-rest) and the derived boolean `has_open_promises` (Schema Gate
Amendment 1, PR #2646); no index holds a pid, uuid, or timestamp. The reply index
is a plain string KV — no hash, no class set, no secondary index — so the
identity-less-hash flood mechanism structurally cannot occur for it.

The daily `has_open_promises` backfill (`Job.backfill_open_promises_index()`,
run from `repair_indexes()` while the bridge and workers are live) writes
*only* that field via `save(update_fields=["has_open_promises"])`. Popoto
excludes IndexedFields from the plain HSET mapping and maintains them through
an atomic Lua EVAL instead, so a save whose entire field list is IndexedFields
sends no `goal` bytes at all — a maintenance pass on this path can never
overwrite a concurrently-written `goal` (#2647).
