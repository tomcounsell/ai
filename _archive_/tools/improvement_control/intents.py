"""Dispatch intents: the durable record between "decided" and "a session exists".

Six states, one transition table (Decision 13, ``_ALLOWED``), one script per
effect (Decision 3): each script re-checks the case's generation and revision
fence (the same rule ``journal.transition`` enforces) before it records
anything, and every accepted effect appends its own journal entry so the
case's history reads as one timeline regardless of which module wrote it.

``admit`` also walks the case's ``intents`` set (never ``KEYS``/``SCAN``) to
refuse admission while any intent on the case is ``reconciliation_required``
-- a wedged case never consumes a lane slot. ``list_intents`` is the one
Python reader over that same set, used by ``resume --force``, ``doctor``,
``case explain``, and ``get_control_status``.

``on_session_terminal`` is ``finalize_session`` step 7's terminal hook
(Decision 6): it releases the lane slot and settles the intent by outcome in
one script, never two calls that could observe each other's half-done state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import redis.exceptions

from tools.improvement_control import keys

logger = logging.getLogger(__name__)

#: The six intent states and the only transitions a CAS script accepts.
#: `admit` writes "admitted" directly (no separate "prepare" step -- a
#: second script would reopen the two-write window this design closes).
#: `on_session_terminal`'s settle branch moves "running" -> "settled"
#: directly. Terminal: "settled", "cancelled". Holding: "reconciliation_required",
#: whose only exit is `cancel`.
INTENT_STATES: tuple[str, ...] = (
    "admitted",
    "materialized",
    "running",
    "settled",
    "cancelled",
    "reconciliation_required",
)

_ALLOWED: dict[str, frozenset[str]] = {
    "admitted": frozenset({"materialized", "reconciliation_required"}),
    "materialized": frozenset({"running", "reconciliation_required"}),
    "running": frozenset({"settled", "reconciliation_required"}),
    "reconciliation_required": frozenset({"cancelled"}),
    "settled": frozenset(),
    "cancelled": frozenset(),
}

#: Namespace-shared prelude every effect script runs before its own effect:
#: schema check, namespace pause, per-case pause, generation fence, revision
#: fence. One Python string constant, concatenated into each of the four
#: script bodies below at import time -- a mutation to THIS constant hits
#: all four at once, by design; Task 12's per-script mutation testing targets
#: each script's own compare *after* concatenation (`generation >=
#: highest_accepted`, `expected_revision == revision`, `from_state`), not
#: this shared prelude.
_LUA_FENCE_PRELUDE = """
local schema = redis.call("GET", KEYS[1])
if schema and schema ~= ARGV[1] then
  return {0, "SCHEMA_MISMATCH", -1}
end
local ns_pause_reason = redis.call("HGET", KEYS[2], "reason")
if ns_pause_reason and ns_pause_reason ~= "" then
  return {0, "PAUSED", -1}
end
local revision = tonumber(redis.call("HGET", KEYS[3], "revision") or "0")
local highest_accepted = tonumber(redis.call("HGET", KEYS[3], "highest_accepted") or "0")
if redis.call("HGET", KEYS[3], "paused") == "1" then
  return {0, "PAUSED", revision}
end
if ARGV[3] < highest_accepted then
  return {0, "STALE_GENERATION", revision}
end
if ARGV[2] ~= revision then
  return {0, "REVISION_MISMATCH", revision}
end
"""

#: KEYS: [schema, ns_pause, head, journal, intents_set, slots]
#: ARGV: [schema_version, expected_revision, generation, action_id,
#:        action_type, request_digest, charter_digest, max_concurrent,
#:        journal_max_entries, intent_key_prefix]
_LUA_ADMIT = (
    _LUA_FENCE_PRELUDE.replace("ARGV[2]", "tonumber(ARGV[2])").replace(
        "ARGV[3]", "tonumber(ARGV[3])"
    )
    + """
local action_id = ARGV[4]
local intent_prefix = ARGV[10]
local intent_key = intent_prefix .. action_id

-- Reconciliation guard: a wedged case admits nothing, checked before the
-- slot count so a wedged case never consumes a slot.
local members = redis.call("SMEMBERS", KEYS[5])
for _, aid in ipairs(members) do
  if redis.call("HGET", intent_prefix .. aid, "state") == "reconciliation_required" then
    return {0, "INTENT_STATE", revision}
  end
end

local slot_count = redis.call("HLEN", KEYS[6])
local max_concurrent = tonumber(ARGV[8])
if slot_count + 1 > max_concurrent then
  return {0, "SLOT_EXHAUSTED", revision}
end

local now = redis.call("TIME")
local now_s = tonumber(now[1])
local new_revision = revision + 1
local generation = tonumber(ARGV[3])

redis.call("HSET", KEYS[3], "revision", new_revision, "highest_accepted", generation,
  "epoch", generation, "updated_at", now_s)
redis.call("HSET", intent_key,
  "state", "admitted",
  "action_type", ARGV[5],
  "request_digest", ARGV[6],
  "charter_digest", ARGV[7],
  "generation", generation,
  "attempts", 0,
  "stale_sweeps", 0,
  "created_ts", now_s,
  "updated_ts", now_s)
redis.call("SADD", KEYS[5], action_id)
redis.call("HSET", KEYS[6], action_id, now_s)

local entry = cjson.encode({revision = new_revision, action_id = action_id,
  event = "intent_admitted", generation = generation, ts = now_s})
redis.call("RPUSH", KEYS[4], entry)
redis.call("LTRIM", KEYS[4], -tonumber(ARGV[9]), -1)

return {1, "OK", new_revision}
"""
)

#: KEYS: [schema, ns_pause, head, journal, intent]
#: ARGV: [schema_version, expected_revision, generation, action_id,
#:        agent_session_id, journal_max_entries]
#: Accepts from "admitted" (the normal path, HINCRBY attempts to 1) and,
#: idempotently, a retry that finds the intent already "materialized" under
#: the SAME bound session id (HINCRBY attempts again -- this is its only
#: writer, Decision 2). Any other observed state is INTENT_STATE.
_LUA_RECORD_MATERIALIZED = (
    _LUA_FENCE_PRELUDE.replace("ARGV[2]", "tonumber(ARGV[2])").replace(
        "ARGV[3]", "tonumber(ARGV[3])"
    )
    + """
local intent_key = KEYS[5]
local action_id = ARGV[4]
local agent_session_id = ARGV[5]
local state = redis.call("HGET", intent_key, "state")
local now = redis.call("TIME")
local now_s = tonumber(now[1])
local new_revision = revision + 1
local generation = tonumber(ARGV[3])

local bound_session = redis.call("HGET", intent_key, "agent_session_id")
if state == "materialized" and bound_session == agent_session_id then
  redis.call("HINCRBY", intent_key, "attempts", 1)
  redis.call("HSET", intent_key, "updated_ts", now_s)
  return {1, "OK", revision}
end
if state ~= "admitted" then
  return {0, "INTENT_STATE", revision}
end

redis.call("HSET", KEYS[3], "revision", new_revision, "highest_accepted", generation,
  "epoch", generation, "updated_at", now_s)
redis.call("HSET", intent_key, "state", "materialized", "agent_session_id", agent_session_id,
  "updated_ts", now_s)
redis.call("HINCRBY", intent_key, "attempts", 1)

local entry = cjson.encode({revision = new_revision, action_id = action_id,
  event = "intent_materialized", generation = generation, ts = now_s})
redis.call("RPUSH", KEYS[4], entry)
redis.call("LTRIM", KEYS[4], -tonumber(ARGV[6]), -1)

return {1, "OK", new_revision}
"""
)

#: KEYS: [schema, ns_pause, head, journal, intent, slots]
#: ARGV: [schema_version, expected_revision, generation, action_id,
#:        from_state, to_state, event, journal_max_entries, reason]
#: Generic CAS mover for the remaining single-hop transitions
#: (record_running, mark_reconciliation_required). `from_state` is passed
#: explicitly by the wrapper, checked against `_ALLOWED` in Python, and
#: CAS-checked by the script. The slot key is always passed (harmless for
#: `record_running`, which never reaches `to_state == "reconciliation_required"`)
#: so the slot release and the `reason` write land in the SAME script call
#: as the state move: no window in which a crash leaves a wedged intent
#: still holding its slot.
_LUA_MOVE_INTENT = (
    _LUA_FENCE_PRELUDE.replace("ARGV[2]", "tonumber(ARGV[2])").replace(
        "ARGV[3]", "tonumber(ARGV[3])"
    )
    + """
local intent_key = KEYS[5]
local slots_key = KEYS[6]
local action_id = ARGV[4]
local from_state = ARGV[5]
local to_state = ARGV[6]
local event = ARGV[7]
local reason = ARGV[9]
local now = redis.call("TIME")
local now_s = tonumber(now[1])
local new_revision = revision + 1
local generation = tonumber(ARGV[3])

if redis.call("HGET", intent_key, "state") ~= from_state then
  return {0, "INTENT_STATE", revision}
end

redis.call("HSET", KEYS[3], "revision", new_revision, "highest_accepted", generation,
  "epoch", generation, "updated_at", now_s)
if to_state == "reconciliation_required" then
  redis.call("HSET", intent_key, "state", to_state, "updated_ts", now_s, "reason", reason)
  redis.call("HDEL", slots_key, action_id)
else
  redis.call("HSET", intent_key, "state", to_state, "updated_ts", now_s)
end

local entry = cjson.encode({revision = new_revision, action_id = action_id,
  event = event, generation = generation, ts = now_s})
redis.call("RPUSH", KEYS[4], entry)
redis.call("LTRIM", KEYS[4], -tonumber(ARGV[8]), -1)

return {1, "OK", new_revision}
"""
)

#: KEYS: [schema, ns_pause, head, journal, intent, slots]
#: ARGV: [schema_version, expected_revision, generation, action_id,
#:        journal_max_entries]
#: `cancel` is the ONLY writer out of "reconciliation_required" (Decision 12
#: Data Flow step 12). The slot HDEL is idempotent -- a wedged intent's slot
#: was very likely already released by `mark_reconciliation_required`.
_LUA_CANCEL = (
    _LUA_FENCE_PRELUDE.replace("ARGV[2]", "tonumber(ARGV[2])").replace(
        "ARGV[3]", "tonumber(ARGV[3])"
    )
    + """
local intent_key = KEYS[5]
local action_id = ARGV[4]
local now = redis.call("TIME")
local now_s = tonumber(now[1])
local new_revision = revision + 1
local generation = tonumber(ARGV[3])

if redis.call("HGET", intent_key, "state") ~= "reconciliation_required" then
  return {0, "INTENT_STATE", revision}
end

redis.call("HSET", KEYS[3], "revision", new_revision, "highest_accepted", generation,
  "epoch", generation, "updated_at", now_s)
redis.call("HSET", intent_key, "state", "cancelled", "updated_ts", now_s)
redis.call("HDEL", KEYS[6], action_id)

local entry = cjson.encode({revision = new_revision, action_id = action_id,
  event = "intent_cancelled", generation = generation, ts = now_s})
redis.call("RPUSH", KEYS[4], entry)
redis.call("LTRIM", KEYS[4], -tonumber(ARGV[5]), -1)

return {1, "OK", new_revision}
"""
)

#: KEYS: [intent, slots]
#: ARGV: [action_id, status, expected_admitted_ts]
#: No case-level fence: this is the terminal hook, called from the worker's
#: own finalize path with no case lease held, and settling an intent must
#: never be blockable by a stale controller (Risk 3). The slot
#: compare-and-delete checks the field's stored `admitted_ts` against what
#: this intent itself recorded at admit time (`created_ts`) rather than
#: trusting the action-id-keyed field alone, so a slot silently repurposed
#: under a reused key (impossible today with UUID action ids, but the
#: interface names the case) reads `foreign_holder` and is left untouched
#: instead of being deleted out from under its real occupant.
_LUA_ON_SESSION_TERMINAL = """
local intent_key = KEYS[1]
local slots_key = KEYS[2]
local action_id = ARGV[1]
local status = ARGV[2]

local slot_result
local stored = redis.call("HGET", slots_key, action_id)
local created_ts = redis.call("HGET", intent_key, "created_ts")
if not stored then
  slot_result = "absent"
elseif created_ts and stored == created_ts then
  redis.call("HDEL", slots_key, action_id)
  slot_result = "released"
else
  slot_result = "foreign_holder"
end

local state = redis.call("HGET", intent_key, "state")
local intent_state = state
local reason = ""
if state == "running" then
  local result_digest = redis.call("HGET", intent_key, "result_digest")
  local now = redis.call("TIME")
  local now_s = tonumber(now[1])
  if result_digest and result_digest ~= "" then
    redis.call("HSET", intent_key, "state", "settled", "updated_ts", now_s)
    intent_state = "settled"
  elseif status == "completed" then
    redis.call("HSET", intent_key, "state", "settled", "reason", "no_proposal", "updated_ts", now_s)
    intent_state = "settled"
    reason = "no_proposal"
  else
    redis.call("HSET", intent_key, "session_terminal_at", now_s)
    intent_state = "running"
  end
end

return {slot_result, intent_state, reason}
"""


@dataclass(frozen=True)
class DispatchIntent:
    """One row of ``improve:{project}:{case}:intent:{action_id}``."""

    action_id: str
    state: str
    action_type: str
    agent_session_id: str | None
    attempts: int
    stale_sweeps: int
    generation: int
    created_ts: float
    updated_ts: float
    session_terminal_at: float | None
    result_digest: str | None
    reason: str


@dataclass(frozen=True)
class IntentResult:
    """The outcome of an intent-lifecycle write. Never an exception."""

    accepted: bool
    reason: str
    revision: int


@dataclass(frozen=True)
class SlotReleaseResult:
    """The outcome of :func:`on_session_terminal`'s slot release half."""

    slot: str  # "released" | "absent" | "foreign_holder"
    intent_state: str
    reason: str


def _control_redis():
    """This package's private Redis alias. See the package docstring."""
    from utils.redis_client import text_redis

    return text_redis()


def _journal_max_entries() -> int:
    from config.settings import settings

    return int(settings.improvement.journal_max_entries)


def _unavailable(exc: Exception) -> IntentResult:
    logger.warning("[improvement-control] intents unavailable: %s", exc)
    return IntentResult(False, "UNAVAILABLE", -1)


def admit(
    project_key: str,
    case_id: str,
    action_id: str,
    *,
    expected_revision: int,
    generation: int,
    action_type: str,
    request_digest: str = "",
    charter_digest: str = "",
    max_concurrent: int,
) -> IntentResult:
    """Admit one action into the lane, reserving a slot in the same script.

    Refuses ``INTENT_STATE`` while any intent on the case is
    ``reconciliation_required`` (checked before the slot count, so a wedged
    case never consumes a slot), ``SLOT_EXHAUSTED`` at the concurrency cap,
    and the same schema/pause/generation/revision reasons ``transition``
    returns.
    """
    intent_prefix = f"improve:{project_key}:{case_id}:intent:"
    try:
        raw = _control_redis().eval(
            _LUA_ADMIT,
            6,
            keys.schema_key(project_key),
            keys.pause_key(project_key),
            keys.head_key(project_key, case_id),
            keys.journal_key(project_key, case_id),
            keys.intents_set_key(project_key, case_id),
            keys.slots_key(project_key),
            str(keys.SCHEMA_VERSION),
            int(expected_revision),
            int(generation),
            action_id,
            action_type,
            request_digest,
            charter_digest,
            int(max_concurrent),
            _journal_max_entries(),
            intent_prefix,
        )
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        return _unavailable(exc)
    accepted, reason, revision = raw
    return IntentResult(bool(accepted), reason, int(revision))


def record_materialized(
    project_key: str,
    case_id: str,
    action_id: str,
    *,
    expected_revision: int,
    generation: int,
    agent_session_id: str,
) -> IntentResult:
    """``admitted -> materialized``; idempotent under a retry for the same session."""
    try:
        raw = _control_redis().eval(
            _LUA_RECORD_MATERIALIZED,
            5,
            keys.schema_key(project_key),
            keys.pause_key(project_key),
            keys.head_key(project_key, case_id),
            keys.journal_key(project_key, case_id),
            keys.intent_key(project_key, case_id, action_id),
            str(keys.SCHEMA_VERSION),
            int(expected_revision),
            int(generation),
            action_id,
            agent_session_id,
            _journal_max_entries(),
        )
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        return _unavailable(exc)
    accepted, reason, revision = raw
    return IntentResult(bool(accepted), reason, int(revision))


def _move(
    project_key: str,
    case_id: str,
    action_id: str,
    *,
    expected_revision: int,
    generation: int,
    from_state: str,
    to_state: str,
    event: str,
    reason: str = "",
) -> IntentResult:
    """Generic single-hop CAS mover. ``from_state`` is always explicit here —
    ``reconciliation_required`` has three valid predecessors (admitted,
    materialized, running), so a target-state-only lookup into ``_ALLOWED``
    cannot recover the caller's actual state and must never be attempted.

    The slot key is always passed; the script only touches it when
    ``to_state == "reconciliation_required"`` (see the script's own comment).
    """
    if to_state not in _ALLOWED.get(from_state, frozenset()):
        raise ValueError(f"{from_state!r} -> {to_state!r} is not a declared transition")
    try:
        raw = _control_redis().eval(
            _LUA_MOVE_INTENT,
            6,
            keys.schema_key(project_key),
            keys.pause_key(project_key),
            keys.head_key(project_key, case_id),
            keys.journal_key(project_key, case_id),
            keys.intent_key(project_key, case_id, action_id),
            keys.slots_key(project_key),
            str(keys.SCHEMA_VERSION),
            int(expected_revision),
            int(generation),
            action_id,
            from_state,
            to_state,
            event,
            _journal_max_entries(),
            reason,
        )
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        return _unavailable(exc)
    accepted, reason_out, revision = raw
    return IntentResult(bool(accepted), reason_out, int(revision))


def record_running(
    project_key: str, case_id: str, action_id: str, *, expected_revision: int, generation: int
) -> IntentResult:
    """``materialized -> running``."""
    return _move(
        project_key,
        case_id,
        action_id,
        expected_revision=expected_revision,
        generation=generation,
        from_state="materialized",
        to_state="running",
        event="intent_running",
    )


def mark_reconciliation_required(
    project_key: str,
    case_id: str,
    action_id: str,
    *,
    expected_revision: int,
    generation: int,
    from_state: str,
    reason: str = "",
) -> IntentResult:
    """Move an ``admitted``/``materialized``/``running`` intent to the holding state.

    ``from_state`` must be one of the three states ``_ALLOWED`` permits into
    ``reconciliation_required``; the script still enforces it as a CAS.

    The slot release and the ``reason`` write happen inside the same script
    call as the state move, so an accepted move always leaves the slot free
    and the reason on the intent hash (``list_intents(...).reason``).
    """
    return _move(
        project_key,
        case_id,
        action_id,
        expected_revision=expected_revision,
        generation=generation,
        from_state=from_state,
        to_state="reconciliation_required",
        event="intent_reconciliation_required",
        reason=reason,
    )


def cancel(
    project_key: str,
    case_id: str,
    action_id: str,
    *,
    expected_revision: int,
    generation: int,
    by: str,
) -> IntentResult:
    """The only exit from ``reconciliation_required`` (Data Flow step 12)."""
    del by  # recorded on the caller's own audit trail, not the journal entry
    try:
        raw = _control_redis().eval(
            _LUA_CANCEL,
            6,
            keys.schema_key(project_key),
            keys.pause_key(project_key),
            keys.head_key(project_key, case_id),
            keys.journal_key(project_key, case_id),
            keys.intent_key(project_key, case_id, action_id),
            keys.slots_key(project_key),
            str(keys.SCHEMA_VERSION),
            int(expected_revision),
            int(generation),
            action_id,
            _journal_max_entries(),
        )
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        return _unavailable(exc)
    accepted, reason, revision = raw
    return IntentResult(bool(accepted), reason, int(revision))


def list_intents(project_key: str, case_id: str) -> list[DispatchIntent]:
    """Every intent on the case, read through its ``intents`` set.

    The one Python reader over the per-case index: ``SMEMBERS`` then
    ``HGETALL`` per id. ``resume --force``, ``doctor``, ``case explain``, and
    ``get_control_status`` all call this rather than scanning the keyspace.
    """
    r = _control_redis()
    action_ids = r.smembers(keys.intents_set_key(project_key, case_id))
    out: list[DispatchIntent] = []
    for action_id in sorted(action_ids):
        raw = r.hgetall(keys.intent_key(project_key, case_id, action_id))
        if not raw:
            continue
        out.append(
            DispatchIntent(
                action_id=action_id,
                state=raw.get("state", ""),
                action_type=raw.get("action_type", ""),
                agent_session_id=raw.get("agent_session_id") or None,
                attempts=int(raw.get("attempts", 0) or 0),
                stale_sweeps=int(raw.get("stale_sweeps", 0) or 0),
                generation=int(raw.get("generation", 0) or 0),
                created_ts=float(raw.get("created_ts", 0) or 0),
                updated_ts=float(raw.get("updated_ts", 0) or 0),
                session_terminal_at=(
                    float(raw["session_terminal_at"]) if raw.get("session_terminal_at") else None
                ),
                result_digest=raw.get("result_digest") or None,
                reason=raw.get("reason", ""),
            )
        )
    return out


def on_session_terminal(session, status: str) -> SlotReleaseResult:
    """``finalize_session`` step 7's terminal hook (Decision 6).

    Releases the lane slot (compare-and-delete against the intent's own
    ``created_ts``) and settles the intent by outcome, in one script. Only a
    ``foreign_holder`` slot result is worth a WARNING; ``absent`` is DEBUG,
    since the reconcile pass already releases a slot before it force-
    finalizes a row (``test_reconcile_forced_finalize_logs_no_warning``).
    """
    extra_context = getattr(session, "extra_context", None) or {}
    action_id = extra_context.get("action_id")
    research_case_id = extra_context.get("research_case_id")
    if not action_id or not research_case_id:
        raise ValueError("on_session_terminal requires action_id and research_case_id")
    project_key = getattr(session, "project_key", None) or "valor"
    try:
        raw = _control_redis().eval(
            _LUA_ON_SESSION_TERMINAL,
            2,
            keys.intent_key(project_key, research_case_id, action_id),
            keys.slots_key(project_key),
            action_id,
            status,
        )
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        logger.warning("[improvement-control] on_session_terminal unavailable: %s", exc)
        return SlotReleaseResult("absent", "", "unavailable")
    slot, intent_state, reason = raw
    if slot == "foreign_holder":
        logger.warning(
            "[improvement-control] slot for action_id=%s held by a different admit at "
            "finalize time; left untouched",
            action_id,
        )
    else:
        logger.debug("[improvement-control] slot release for action_id=%s: %s", action_id, slot)
    return SlotReleaseResult(slot, intent_state, reason)


def dead_letter_exhausted(
    project_key: str, case_id: str, intent: DispatchIntent, *, reason: str
) -> None:
    """Record an exhausted intent's payload in the shared dead-letter model.

    Uses the reserved ``improve_intent`` stage (``bridge/dead_letters.py``'s
    ``STAGES``, "Reserved for the improvement control plane (#3177)") through
    the canonical ``bridge.dead_letters.record`` helper rather than
    ``DeadLetter.create`` directly, so ``created_at``/index-bumping stay in
    one place.
    """
    from bridge.dead_letters import record

    record(
        stage="improve_intent",
        payload={
            "case_id": case_id,
            "action_id": intent.action_id,
            "action_type": intent.action_type,
            "agent_session_id": intent.agent_session_id,
        },
        reason=reason,
        replayable=False,
        project_key=project_key,
    )
