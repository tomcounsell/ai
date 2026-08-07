"""Job — a responsibility to complete something end to end.

Schema (ratified one-shot by the Task 5 schema-gate ruling of
``docs/plans/durability-room-job-agentrun.md``):

- KeyField set = ``{id, room_id}`` — ``id`` (``AutoKeyField``) for identity;
  ``room_id`` (``KeyField``, composite ``{project_key}|{addressee}`` from
  :func:`models.room.room_id`) so a single recency
  ``SortedField(partition_by="room_id")`` serves the top-N bind-or-mint
  candidate lookup without an unbounded index.
- ``status`` is the ONLY IndexedField — low-cardinality (``active`` /
  ``at-rest``), serving the at-rest-with-open-promise query.
- ``goal`` is an **append-only-versioned plain field** (JSON). The router
  never model-authors it: at mint it holds only the mechanical placeholder
  (:func:`mint_placeholder_goal`), so ``goal`` is never null and the
  synchronous bind-or-mint path never blocks. Authoring the real goal (v1)
  is the PM's mandated first step on the Job. The PM's promises live on the
  goal as appended/removed entries — a removal appends ``removed_ts`` rather
  than deleting, so the full promise history stays reconstructable.
- **Never hard-closed.** A Job goes to rest by age (``sweep_to_rest`` on the
  session-health cadence, threshold ``JOB_AT_REST_AGE_SECONDS``) and is
  revived by any new steering message regardless of age (``revive``). This
  revival-after-apparent-completion is user-visible behavior, documented in
  ``docs/features/durability-model.md``.
- Immortal: no ``Meta.ttl`` — the Job is the durable record of what was
  promised and delivered; it must outlive every session that served it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime

from popoto import AutoKeyField, Field, IndexedField, KeyField, Model, SortedField

logger = logging.getLogger(__name__)

GOAL_PLACEHOLDER_PREFIX = "handle user message '"

# Mechanical placeholder truncation length (chars of the triggering message).
# Fixed by the plan's mint contract: `handle user message '<first 20 chars>…'`.
_PLACEHOLDER_CHARS = 20

_EPOCH = datetime(1970, 1, 2, tzinfo=UTC)

# Age threshold after which an idle active Job goes to rest (rest-by-age,
# applied by ``Job.sweep_to_rest`` on the session-health cadence).
# GRAIN OF SALT: provisional/tunable via env — sized to keep multi-day
# conversations active across a weekend while letting abandoned threads
# reach the at-rest-with-open-promise backstop within the week.
JOB_AT_REST_AGE_SECONDS = int(os.environ.get("JOB_AT_REST_AGE_SECONDS", str(72 * 3600)))


def _now() -> datetime:
    return datetime.now(tz=UTC)


def mint_placeholder_goal(message_text: str) -> str:
    """The mechanical mint-time goal — router-seeded, never model-authored.

    ``handle user message '<first 20 chars>…'`` per the plan's mint contract;
    the ellipsis appears only when the message was actually truncated. Never
    returns an empty goal, even for an empty trigger message.
    """
    text = (message_text or "").strip()
    head = text[:_PLACEHOLDER_CHARS]
    suffix = "…" if len(text) > _PLACEHOLDER_CHARS else ""
    return f"{GOAL_PLACEHOLDER_PREFIX}{head}{suffix}'"


class Job(Model):
    """A responsibility to complete something end to end. Never hard-closed."""

    id = AutoKeyField()
    room_id = KeyField()
    # Low-cardinality status — the only IndexedField (active | at-rest).
    status = IndexedField(default="active")
    # Recency for the top-N bind-or-mint candidate lookup; partitioned by
    # room_id so the sorted set stays per-Room (the proven
    # AgentSession.created_at partition_by="project_key" pattern).
    last_active_at = SortedField(type=datetime, partition_by="room_id")
    # Append-only-versioned goal + promises, JSON:
    #   {"versions": [{"ts", "author", "text"}, ...],
    #    "promises": [{"id", "ts", "text", "removed_ts"}, ...]}
    goal = Field(null=True)

    # -- Identity -----------------------------------------------------------

    @property
    def job_id(self) -> str:
        return str(self.id)

    # -- Mint ---------------------------------------------------------------

    @classmethod
    def mint(cls, room_id: str, message_text: str, *, author: str = "router") -> Job:
        """Mint a NEW Job with a single goal v1 in one save.

        ``author="router"`` (the default, used by the bind-or-mint router):
        the goal v1 is only the mechanical placeholder — the router is not
        smart enough to author a real goal; the PM authors it as its
        mandated first step (enforced in the PM priming and nudged by the
        outbound advisory pass).

        ``author="pm"`` (used by ``tools/job_tool create``): ``message_text``
        IS the PM-authored goal, stored verbatim as v1 —
        ``goal_is_placeholder()`` is False from birth.
        """
        goal_text = (
            mint_placeholder_goal(message_text)
            if author == "router"
            else (message_text or "").strip()
        )
        job = cls(
            room_id=room_id,
            status="active",
            last_active_at=_now(),
        )
        job._write_goal_data(
            {
                "versions": [
                    {
                        "ts": _now().isoformat(),
                        "author": author,
                        "text": goal_text,
                    }
                ],
                "promises": [],
            },
            save=False,
        )
        job.save()
        return job

    # -- Goal (append-only versioned) ---------------------------------------

    def _goal_data(self) -> dict:
        try:
            data = json.loads(self.goal) if self.goal else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("[job] invalid goal JSON on %s; treating as empty", self.job_id)
            data = {}
        data.setdefault("versions", [])
        data.setdefault("promises", [])
        return data

    def _write_goal_data(self, data: dict, *, save: bool = True) -> None:
        self.goal = json.dumps(data)
        if save:
            self.save()

    def goal_versions(self) -> list[dict]:
        """All goal versions, oldest first."""
        return list(self._goal_data()["versions"])

    def current_goal(self) -> str:
        """The newest goal version's text (never null after mint)."""
        versions = self._goal_data()["versions"]
        return versions[-1]["text"] if versions else ""

    def goal_is_placeholder(self) -> bool:
        """True while the goal is still the mechanical router-seeded mint text.

        The PM's goal-authoring mandate (priming + outbound advisory nudge)
        keys on this: any PM-appended version clears it.
        """
        versions = self._goal_data()["versions"]
        if len(versions) != 1:
            return False
        only = versions[0]
        return only.get("author") == "router" and str(only.get("text", "")).startswith(
            GOAL_PLACEHOLDER_PREFIX
        )

    def append_goal_version(self, text: str, *, author: str) -> None:
        """Append a new goal version (never overwrites prior versions)."""
        data = self._goal_data()
        data["versions"].append({"ts": _now().isoformat(), "author": author, "text": text})
        self._write_goal_data(data)

    # -- Promises (PM-authored, appended/removed as goal entries) -----------

    def add_promise(self, text: str) -> str:
        """Append an open promise entry; returns its id."""
        data = self._goal_data()
        promise_id = uuid.uuid4().hex[:12]
        data["promises"].append(
            {
                "id": promise_id,
                "ts": _now().isoformat(),
                "text": text,
                "removed_ts": None,
            }
        )
        self._write_goal_data(data)
        return promise_id

    def remove_promise(self, promise_id: str) -> bool:
        """Discharge a promise. Append-only: stamps ``removed_ts``, keeps the entry."""
        data = self._goal_data()
        for entry in data["promises"]:
            if entry.get("id") == promise_id and entry.get("removed_ts") is None:
                entry["removed_ts"] = _now().isoformat()
                self._write_goal_data(data)
                return True
        return False

    def open_promises(self) -> list[dict]:
        return [p for p in self._goal_data()["promises"] if p.get("removed_ts") is None]

    def all_promises(self) -> list[dict]:
        return list(self._goal_data()["promises"])

    # -- Lifecycle (rest by age, revived by any steer; never hard-closed) ---

    def touch(self) -> None:
        """Record activity (message bound, PM turn) — refreshes recency."""
        self.last_active_at = _now()
        self.save()

    def mark_at_rest(self) -> None:
        self.status = "at-rest"
        self.save()

    def revive(self) -> None:
        """Any steering message revives a Job regardless of age."""
        self.status = "active"
        self.last_active_at = _now()
        self.save()

    # -- Queries ------------------------------------------------------------

    @classmethod
    def recent_for_room(cls, room_id: str, *, limit: int = 5) -> list[Job]:
        """Top-N most recent Jobs in a Room, newest first.

        Served by the ``last_active_at`` SortedField partition — the range
        scan is per-Room, never a global index walk.
        """
        try:
            jobs = list(cls.query.filter(room_id=room_id, last_active_at__gte=_EPOCH))
        except Exception as e:  # noqa: BLE001 — candidate lookup must fail open
            logger.warning("[job] recent_for_room failed for %s: %s", room_id, e)
            return []
        jobs.sort(key=lambda j: j.last_active_at or _EPOCH, reverse=True)
        return jobs[:limit]

    @classmethod
    def sweep_to_rest(cls, now: float | None = None) -> int:
        """Rest-by-age: transition idle active Jobs to ``at-rest`` via the ORM.

        Runs on the session-health cadence (invoked by
        ``agent/session_health.py::_check_jobs_at_rest_with_open_promises``
        immediately before the open-promise scan, so the backstop always
        evaluates fresh rest state — never correct logic over empty input).
        An active Job whose ``last_active_at`` is older than
        ``JOB_AT_REST_AGE_SECONDS`` goes to rest through ``mark_at_rest()``
        (a normal ORM save, so INDEX_SWAP_LUA moves the ``status`` index
        membership). Rest is never terminal — any reply revives the Job.

        Returns the number of Jobs transitioned.
        """
        rested = 0
        try:
            from bridge.utc import to_unix_ts

            now_ts = now if now is not None else time.time()
            cutoff = now_ts - JOB_AT_REST_AGE_SECONDS
            for job in cls.query.filter(status="active"):
                try:
                    last_ts = to_unix_ts(job.last_active_at)
                    if last_ts is not None and last_ts < cutoff:
                        job.mark_at_rest()
                        rested += 1
                        logger.info(
                            "[job] rest-by-age: job %s (room=%s) idle %.0fs > %ds",
                            job.job_id,
                            job.room_id,
                            now_ts - last_ts,
                            JOB_AT_REST_AGE_SECONDS,
                        )
                except Exception as e:  # noqa: BLE001 — one bad row must not stop the sweep
                    logger.warning("[job] rest sweep failed for %s: %s", job.job_id, e)
        except Exception as e:  # noqa: BLE001 — sweep never raises into the caller
            logger.warning("[job] rest-by-age sweep failed: %s", e)
        return rested

    @classmethod
    def at_rest_with_open_promises(cls) -> list[Job]:
        """Jobs at rest that still carry an open promise entry.

        The at-rest health backstop (``agent/session_health.py``) surfaces
        these to the operator surface only — never to human chat.
        """
        flagged = []
        try:
            for job in cls.query.filter(status="at-rest"):
                if job.open_promises():
                    flagged.append(job)
        except Exception as e:  # noqa: BLE001 — a backstop query never raises
            logger.warning("[job] at_rest_with_open_promises query failed: %s", e)
        return flagged

    # -- Guarded index repair (Risk 2 / #2207) ------------------------------

    _repair_lock = threading.Lock()

    @classmethod
    def repair_indexes(cls) -> tuple[int, int]:
        """Guarded repair path — the ONLY sanctioned index rebuild for Job.

        Job is listed in ``scripts/popoto_index_cleanup._GUARDED_ELSEWHERE``
        so the generic raw ``rebuild_indexes()`` sweep never touches it; this
        method is what the daily cleanup reflection invokes instead.

        Guard, two legs:

        1. **DELETES identity-less ``Job:*`` hashes** — records missing the
           ``room_id`` KeyField — BEFORE any rebuild, so a phantom hash can
           never be re-indexed into the class set or the ``status``
           ``$IndexF`` set on every rebuild (the #2207 flood mechanism).
           Safe to delete rather than skip (the ``AgentSession.repair_indexes``
           posture) because a Job hash without its KeyField data is
           unreachable through the ORM by construction — the KeyField pair
           IS the primary key — and carries no payload worth preserving
           forensically.
        2. **Clears every ``$IndexF:Job:*`` key** before the rebuild. This
           leg is LOAD-BEARING for the ``status`` IndexedField: popoto's
           ``rebuild_indexes()`` never enumerates ``$IndexF`` sets, and the
           raw hash delete in leg 1 bypasses ``on_delete``'s SREM — so
           without this leg, a quarantined hash's index membership (and any
           gone-hash orphan) would leak permanently. Stale members (whose
           backing hash no longer exists) are counted before deletion;
           ``rebuild_indexes()`` reconstructs the sets from the surviving
           healthy hashes. Mirrors ``AgentSession.repair_indexes()``'s
           stale-member scan (``models/agent_session.py``). ``Room``
           legitimately lacks this leg — it has zero IndexedFields.

        Returns ``(quarantined_count, rebuilt_count)`` — same arity as
        ``Room.repair_indexes()``. Stale ``$IndexF`` members are logged.
        """
        from popoto.redis_db import POPOTO_REDIS_DB

        from config.popoto_floor import assert_popoto_floor

        # Below-floor popoto must never reach the destructive rebuild
        # (see config/popoto_floor.py / issue #2536).
        assert_popoto_floor()

        if not cls._repair_lock.acquire(blocking=False):
            logger.warning("[job] repair_indexes already running; skipping this invocation")
            return (0, 0)
        try:
            quarantined = 0
            cursor = 0
            while True:
                cursor, keys = POPOTO_REDIS_DB.scan(cursor=cursor, match="Job:*", count=500)
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else str(key)
                    if "::" in key_str:
                        continue  # companion key, not a record hash
                    if POPOTO_REDIS_DB.type(key) != b"hash":
                        continue
                    fields = POPOTO_REDIS_DB.hgetall(key)
                    decoded = {(k.decode() if isinstance(k, bytes) else k) for k in fields}
                    if "room_id" not in decoded:
                        POPOTO_REDIS_DB.delete(key)
                        quarantined += 1
                        logger.warning(
                            "[job] quarantined identity-less Job hash %s "
                            "(missing KeyField data — would re-index as a phantom)",
                            key_str,
                        )
                if cursor == 0:
                    break

            # Leg 2: clear $IndexF:Job:* keys (popoto's rebuild_indexes never
            # enumerates them, and leg 1's raw delete bypassed on_delete's
            # SREM). Count stale members first so drift is observable, then
            # delete the whole key — rebuild_indexes() reconstructs it from
            # the surviving healthy hashes.
            stale_members = 0
            for index_key in POPOTO_REDIS_DB.keys("$IndexF:Job:*"):
                for member in POPOTO_REDIS_DB.smembers(index_key):
                    if not POPOTO_REDIS_DB.exists(member):
                        stale_members += 1
                POPOTO_REDIS_DB.delete(index_key)
            if stale_members:
                logger.warning(
                    "[job] cleared %d stale $IndexF member(s) pointing at gone hashes",
                    stale_members,
                )

            rebuilt = cls.rebuild_indexes()
            return (quarantined, rebuilt if isinstance(rebuilt, int) else 0)
        finally:
            cls._repair_lock.release()
