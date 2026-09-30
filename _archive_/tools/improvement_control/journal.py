"""The control journal: one head, one bounded list, one transition script.

The head (``keys.head_key``) is the authoritative state of a case; the
journal (``keys.journal_key``) is its bounded history. :func:`transition` is
the single writer of both, in one Lua ``EVAL`` that re-checks schema, pause,
fencing generation, and revision before recording anything — so a caller
either lands cleanly or changes nothing at all. See the package docstring for
the reason-code vocabulary and the private-alias rule this module follows
(:func:`_control_redis`).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import redis.exceptions

from tools.improvement_control import keys

logger = logging.getLogger(__name__)

#: Case-lifecycle events this module's own `transition` accepts. Other
#: modules in this package (`intents.py`) append their own journal entries
#: for intent-lifecycle events (`intent_admitted`, `intent_cancelled`, ...)
#: through their own scripts; those event names are not in this set because
#: they never flow through this function. `state_changed` is the one writer
#: of the head's `state` field after the seed (see :func:`set_state`).
#:
#: Lane 5 (#3217) records the research cycle through this same function, one
#: event per step the planner tick, the evaluation, and the unblock pass
#: take. The set stays closed: an event the cycle does not name is still
#: refused `INVALID_ARGUMENT` before any Redis call.
KNOWN_EVENTS: frozenset[str] = frozenset(
    {
        "action_proposed",
        "paused",
        "resumed",
        "amendment_proposed",
        "state_changed",
        # lane 5 (#3217): the first complete research cycle
        "ranking_recorded",
        "case_opened",
        "evidence_attached",
        "evidence_attached_to_rejected",
        "investigation_opened",
        "hypothesis_proposed",
        "experiment_frozen",
        "verdict_applied",
        "case_unblocked",
    }
)

#: One EVAL per Decision 3: schema check, pause check, generation compare,
#: revision compare, the session-intent binding compare when a session
#: presents one, head advance, and the bounded journal append — all in one
#: atomic call. KEYS: [schema, ns_pause, head, journal, intent]. ARGV:
#: [schema_version, expected_revision, generation, action_id, event,
#: payload_digest, agent_session_id, journal_max_entries, case_state,
#: action_type, artifact_ref].
#:
#: Head `state` contract: the head is the authority for case lifecycle state
#: and has exactly two writers inside this script. A `state_changed` event
#: sets it to the event's `payload_digest` (the new state name); every other
#: accepted transition seeds it from the ORM row's own `state` (ARGV
#: `case_state`) only while the stored value is empty, so a head written
#: before its row existed is re-seeded on its next accepted write rather
#: than pinned to "" forever. `projection.apply`/`replay` copy the head's
#: `state` and `revision` onto `ImprovementCase`; a direct ORM save of
#: `state` is overwritten by the next apply.
_LUA_TRANSITION = """
local schema_key = KEYS[1]
local ns_pause_key = KEYS[2]
local head_key = KEYS[3]
local journal_key = KEYS[4]
local intent_key = KEYS[5]

local schema_version = ARGV[1]
local expected_revision = tonumber(ARGV[2])
local generation = tonumber(ARGV[3])
local action_id = ARGV[4]
local event = ARGV[5]
local payload_digest = ARGV[6]
local agent_session_id = ARGV[7]
local max_entries = tonumber(ARGV[8])
local case_state = ARGV[9]
local action_type = ARGV[10]
local artifact_ref = ARGV[11]

local schema = redis.call("GET", schema_key)
if schema then
  if schema ~= schema_version then
    return {0, "SCHEMA_MISMATCH", -1}
  end
else
  redis.call("SET", schema_key, schema_version)
end

local ns_pause_reason = redis.call("HGET", ns_pause_key, "reason")
if ns_pause_reason and ns_pause_reason ~= "" then
  return {0, "PAUSED", -1}
end

local revision = tonumber(redis.call("HGET", head_key, "revision") or "0")
local highest_accepted = tonumber(redis.call("HGET", head_key, "highest_accepted") or "0")
local head_paused = redis.call("HGET", head_key, "paused")

if head_paused == "1" and event ~= "resumed" then
  return {0, "PAUSED", revision}
end

if generation < highest_accepted then
  return {0, "STALE_GENERATION", revision}
end

if expected_revision ~= revision then
  return {0, "REVISION_MISMATCH", revision}
end

if agent_session_id ~= "" then
  local intent_state = redis.call("HGET", intent_key, "state")
  local intent_session = redis.call("HGET", intent_key, "agent_session_id")
  if intent_state ~= "running" or intent_session ~= agent_session_id then
    return {0, "INTENT_STATE", revision}
  end
  redis.call("HSET", intent_key, "result_digest", payload_digest)
end

local now = redis.call("TIME")
local now_s = tonumber(now[1])
local new_revision = revision + 1

-- Head `state`: written by `state_changed`, otherwise seeded from the ORM
-- row while the stored value is empty (module docstring for the contract).
local stored_state = redis.call("HGET", head_key, "state")
if event == "state_changed" then
  redis.call("HSET", head_key, "state", payload_digest)
elseif (not stored_state or stored_state == "") and case_state ~= "" then
  redis.call("HSET", head_key, "state", case_state)
end
redis.call(
  "HSET", head_key,
  "revision", new_revision,
  "highest_accepted", generation,
  "epoch", generation,
  "updated_at", now_s
)
if agent_session_id ~= "" then
  redis.call("HSET", intent_key, "updated_ts", now_s)
end
if event == "paused" then
  redis.call("HSET", head_key, "paused", "1", "pause_reason", payload_digest)
elseif event == "resumed" then
  redis.call("HSET", head_key, "paused", "0", "pause_reason", "")
end

local entry = cjson.encode({
  revision = new_revision,
  action_id = action_id,
  event = event,
  payload_digest = payload_digest,
  generation = generation,
  action_type = action_type,
  artifact_ref = artifact_ref,
  ts = now_s,
})
redis.call("RPUSH", journal_key, entry)
redis.call("LTRIM", journal_key, -max_entries, -1)

return {1, "OK", new_revision}
"""


@dataclass(frozen=True)
class Head:
    """One case's authoritative state, read from its Redis hash."""

    revision: int
    state: str
    epoch: int
    highest_accepted: int
    owner: str
    updated_at: float
    paused: bool
    pause_reason: str


@dataclass(frozen=True)
class TransitionResult:
    """The outcome of one :func:`transition` call. Never an exception."""

    accepted: bool
    reason: str
    revision: int


def _control_redis():
    """This package's private Redis alias. See the package docstring."""
    from utils.redis_client import text_redis

    return text_redis()


def ensure_schema(project_key: str) -> None:
    """Write the namespace schema version once, idempotently."""
    _control_redis().setnx(keys.schema_key(project_key), str(keys.SCHEMA_VERSION))


def read_head(project_key: str, case_id: str) -> Head | None:
    """The case's current head, or None when no head has been written."""
    raw = _control_redis().hgetall(keys.head_key(project_key, case_id))
    if not raw:
        return None
    return Head(
        revision=int(raw.get("revision", 0) or 0),
        state=raw.get("state", "") or "",
        epoch=int(raw.get("epoch", 0) or 0),
        highest_accepted=int(raw.get("highest_accepted", 0) or 0),
        owner=raw.get("owner", "") or "",
        updated_at=float(raw.get("updated_at", 0) or 0),
        paused=raw.get("paused") == "1",
        pause_reason=raw.get("pause_reason", "") or "",
    )


def journal_tail(project_key: str, case_id: str, n: int) -> list[dict]:
    """The last ``n`` journal entries, oldest first, as decoded dicts."""
    raw = _control_redis().lrange(keys.journal_key(project_key, case_id), -n, -1)
    return [json.loads(entry) for entry in raw]


def journal_length(project_key: str, case_id: str) -> int:
    return int(_control_redis().llen(keys.journal_key(project_key, case_id)))


def _current_case_state(project_key: str, case_id: str) -> str:
    """The case's own ``ImprovementCase.state`` at call time, the value the
    transition script seeds onto a head whose stored ``state`` is empty. A
    missing or unreadable row yields "", which the script treats as "nothing
    to seed with" and leaves the stored value alone.
    """
    try:
        from models.improvement_case import ImprovementCase

        case = ImprovementCase.query.get(project_key=project_key, id=case_id)
    except Exception:
        return ""
    return (getattr(case, "state", "") or "") if case is not None else ""


def transition(
    project_key: str,
    case_id: str,
    *,
    expected_revision: int | None,
    generation: int,
    event: str,
    payload_digest: str = "",
    action_id: str = "",
    agent_session_id: str | None = None,
    journal_max_entries: int | None = None,
    action_type: str = "",
    artifact_ref: str = "",
) -> TransitionResult:
    """Advance the case head by one event, or refuse with a reason code.

    ``agent_session_id`` given means a research session is the writer: the
    script additionally requires ``intent:{action_id}.state == "running"``
    and ``intent:{action_id}.agent_session_id == agent_session_id`` inside
    the same call, refusing ``INTENT_STATE`` otherwise (Race 4b). Omit it for
    a controller write (the lease generation is the only fence in that case,
    Decision 12).

    ``action_type`` and ``artifact_ref`` are recorded on the journal entry
    only: the adapter reads ``action_type`` from the entry when it admits the
    proposal, and ``export`` walks the tail for every ``artifact_ref``.
    Neither carries fencing meaning of its own.
    """
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
        return TransitionResult(False, "INVALID_ARGUMENT", -1)
    if expected_revision < 0:
        return TransitionResult(False, "INVALID_ARGUMENT", -1)
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        return TransitionResult(False, "INVALID_ARGUMENT", -1)
    if not event or event not in KNOWN_EVENTS:
        return TransitionResult(False, "INVALID_ARGUMENT", -1)
    if not payload_digest and event not in ("resumed",):
        return TransitionResult(False, "INVALID_ARGUMENT", -1)
    if agent_session_id and not action_id:
        return TransitionResult(False, "INVALID_ARGUMENT", -1)

    if journal_max_entries is None:
        from config.settings import settings

        journal_max_entries = settings.improvement.journal_max_entries

    case_state = _current_case_state(project_key, case_id)

    try:
        raw = _control_redis().eval(
            _LUA_TRANSITION,
            5,
            keys.schema_key(project_key),
            keys.pause_key(project_key),
            keys.head_key(project_key, case_id),
            keys.journal_key(project_key, case_id),
            keys.intent_key(project_key, case_id, action_id or "_none"),
            str(keys.SCHEMA_VERSION),
            expected_revision,
            generation,
            action_id,
            event,
            payload_digest,
            agent_session_id or "",
            int(journal_max_entries),
            case_state,
            action_type,
            artifact_ref,
        )
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        logger.warning("[improvement-control] journal unavailable: %s", exc)
        return TransitionResult(False, "UNAVAILABLE", -1)

    accepted, reason, revision = raw
    return TransitionResult(bool(accepted), reason, int(revision))


def pause(
    project_key: str, case_id: str | None, *, generation: int = 0, reason: str, by: str
) -> TransitionResult:
    """Pause one case, or the whole namespace when ``case_id`` is None.

    A namespace-wide pause is a direct hash write (no case revision to
    fence against, since it spans every case) rather than a journal event;
    the per-case pause goes through :func:`transition` like any other event
    and is fenced by the case's own revision and generation.
    """
    if case_id is None:
        try:
            _control_redis().hset(
                keys.pause_key(project_key),
                mapping={"reason": reason, "by": by, "ts": str(time.time())},
            )
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            logger.warning("[improvement-control] journal unavailable: %s", exc)
            return TransitionResult(False, "UNAVAILABLE", -1)
        return TransitionResult(True, "OK", -1)
    head = read_head(project_key, case_id)
    expected_revision = head.revision if head is not None else 0
    return transition(
        project_key,
        case_id,
        expected_revision=expected_revision,
        generation=generation,
        event="paused",
        payload_digest=reason or "paused",
    )


def resume(
    project_key: str, case_id: str | None, *, generation: int = 0, by: str
) -> TransitionResult:
    """Resume one case, or clear a namespace-wide pause when ``case_id`` is None."""
    if case_id is None:
        try:
            _control_redis().delete(keys.pause_key(project_key))
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            logger.warning("[improvement-control] journal unavailable: %s", exc)
            return TransitionResult(False, "UNAVAILABLE", -1)
        return TransitionResult(True, "OK", -1)
    head = read_head(project_key, case_id)
    expected_revision = head.revision if head is not None else 0
    return transition(
        project_key,
        case_id,
        expected_revision=expected_revision,
        generation=generation,
        event="resumed",
        payload_digest="",
    )


def set_state(
    project_key: str, case_id: str, *, generation: int, state: str, by: str
) -> TransitionResult:
    """Move the case to lifecycle ``state`` through the journal.

    The lifecycle owner's one door to the head's ``state`` field: a
    ``state_changed`` event whose ``payload_digest`` is the new state name.
    Refuses ``INVALID_ARGUMENT`` for a name outside ``CASE_STATES``. The
    caller projects the new state with ``projection.apply`` once accepted.
    """
    from models.improvement_case import CASE_STATES

    del by  # recorded on the caller's own audit trail, not the journal entry
    if state not in CASE_STATES:
        return TransitionResult(False, "INVALID_ARGUMENT", -1)
    head = read_head(project_key, case_id)
    expected_revision = head.revision if head is not None else 0
    return transition(
        project_key,
        case_id,
        expected_revision=expected_revision,
        generation=generation,
        event="state_changed",
        payload_digest=state,
    )
