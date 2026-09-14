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
#: they never flow through this function.
KNOWN_EVENTS: frozenset[str] = frozenset(
    {"action_proposed", "paused", "resumed", "amendment_proposed"}
)

#: One EVAL per Decision 3: schema check, pause check, generation compare,
#: revision compare, the session-intent binding compare when a session
#: presents one, head advance, and the bounded journal append — all in one
#: atomic call. KEYS: [schema, ns_pause, head, journal, intent]. ARGV:
#: [schema_version, expected_revision, generation, action_id, event,
#: payload_digest, agent_session_id, journal_max_entries, case_state,
#: action_type, artifact_ref].
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

-- Blocker fix (#3315 review): no script ever wrote the head's own `state`
-- field, so `read_head` always returned "" and `projection.apply`/`replay`
-- clobbered `ImprovementCase.state` with it. Seeded once, on whichever
-- transition call is the case's first (HSETNX never overwrites a value a
-- later writer already set); this lane never changes case lifecycle state
-- itself, so there is nothing to keep resyncing after the seed.
redis.call("HSETNX", head_key, "state", case_state)
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
    """The case's own ``ImprovementCase.state`` at call time, seeded onto the
    head by :func:`transition`'s script (blocker fix, #3315 review: the head
    otherwise never carries a ``state`` at all). A missing or unreadable row
    seeds "" -- no worse than the pre-fix behavior, and every production
    caller of ``transition`` acts on a case that already has an ORM row.
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

    ``action_type`` is recorded on the journal entry only (tech debt, #3315
    review: it was parsed by the CLI and silently dropped, so the adapter's
    ``_unadmitted_proposal`` always defaulted to ``"investigate"``); it
    carries no fencing meaning of its own.
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
