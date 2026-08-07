"""Job — a responsibility to complete something end to end.

Schema (ratified one-shot by the Task 5 schema-gate ruling of
``docs/plans/durability-room-job-agentrun.md``):

- KeyField set = ``{id, room_id}`` — ``id`` (``AutoKeyField``) for identity;
  ``room_id`` (``KeyField``, composite ``{project_key}|{addressee}`` from
  :func:`models.room.room_id`) so a single recency
  ``SortedField(partition_by="room_id")`` serves the top-N bind-or-mint
  candidate lookup without an unbounded index.
- Two IndexedFields, both low-cardinality: ``status`` (``active`` /
  ``at-rest``) and ``has_open_promises`` (two-valued bool). Their
  intersection serves the at-rest-with-open-promise backstop without
  scanning the at-rest population. The schema gate's rule is a cardinality
  rule — never index a pid, uuid, or timestamp — and both honor it
  (Schema Gate Amendment 1, ``docs/plans/job-model-scaling-followups.md``).
  ``has_open_promises`` is a derived projection of ``goal``, never
  authoritative: every read that matters re-verifies with
  :meth:`Job.open_promises`.
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
    # Low-cardinality status (active | at-rest).
    status = IndexedField(default="active")
    # Recency for the top-N bind-or-mint candidate lookup; partitioned by
    # room_id so the sorted set stays per-Room (the proven
    # AgentSession.created_at partition_by="project_key" pattern).
    last_active_at = SortedField(type=datetime, partition_by="room_id")
    # Append-only-versioned goal + promises, JSON:
    #   {"versions": [{"ts", "author", "text"}, ...],
    #    "promises": [{"id", "ts", "text", "removed_ts"}, ...]}
    goal = Field(null=True)
    # Derived projection of `goal`, maintained at the _write_goal_data
    # chokepoint so it cannot be bypassed. Bounds the at-rest backstop to an
    # index intersection instead of a scan of the whole at-rest population.
    # `type=bool` is load-bearing: without it the value hydrates as the
    # string "False", which is truthy.
    has_open_promises = IndexedField(type=bool, default=False)

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
        # Every promise mutation funnels through here (mint, add_promise,
        # remove_promise, append_goal_version), so deriving the index flag at
        # this point makes it un-bypassable rather than a discipline callers
        # have to remember.
        self.has_open_promises = any(
            entry.get("removed_ts") is None for entry in data.get("promises", [])
        )
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

        Served by intersecting the ``status`` and ``has_open_promises``
        index sets, so the work is proportional to the flagged set rather
        than to the at-rest population. That population has no upper bound:
        rest-by-age bounds the *active* set, nothing retires an at-rest Job,
        and this runs every 300 seconds per worker. The old form hydrated
        every at-rest Job and ``json.loads``-ed its ``goal`` on each pass.

        ``has_open_promises`` is a derived projection, so each candidate is
        re-verified against :meth:`open_promises` — the ``goal`` JSON stays
        the single source of truth, and a stale flag can only cost a
        hydration, never a wrong answer.
        """
        flagged = []
        try:
            for job in cls.query.filter(status="at-rest", has_open_promises=True):
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
            #
            # Existence checks are pipelined in batches — a bloated index
            # (hundreds of thousands to millions of stale pointers) turns a
            # one-round-trip-per-member scan into a multi-hour hang, and this
            # runs on the daily maintenance path. Mirrors
            # ``AgentSession.repair_indexes``' batching.
            stale_members = 0
            batch_size = 5000
            for index_key in POPOTO_REDIS_DB.keys("$IndexF:Job:*"):
                members = list(POPOTO_REDIS_DB.smembers(index_key))
                for i in range(0, len(members), batch_size):
                    batch = members[i : i + batch_size]
                    pipe = POPOTO_REDIS_DB.pipeline(transaction=False)
                    for member in batch:
                        pipe.exists(member)
                    stale_members += sum(1 for exists in pipe.execute() if not exists)
                POPOTO_REDIS_DB.delete(index_key)
            if stale_members:
                logger.warning(
                    "[job] cleared %d stale $IndexF member(s) pointing at gone hashes",
                    stale_members,
                )

            rebuilt = cls.rebuild_indexes()
            cls.backfill_open_promises_index()
            return (quarantined, rebuilt if isinstance(rebuilt, int) else 0)
        finally:
            cls._repair_lock.release()

    @classmethod
    def backfill_open_promises_index(cls) -> int:
        """Stamp ``has_open_promises`` on Jobs whose stored flag disagrees with ``goal``.

        This is a daily re-derivation, not a one-shot legacy migration:
        ``rebuild_indexes()`` runs immediately before this method on the same
        maintenance path (see :meth:`repair_indexes`) and already stamps every
        hash — including legacy rows with no ``has_open_promises`` attribute —
        via ``model_class(**model_attrs)``, which fills the missing field from
        ``default=False`` and calls ``on_save`` for every field. So by the time
        this loop runs, every row already has a flag; what this loop catches is
        any row whose flag has since drifted from its ``goal`` for any reason,
        making a wrongly-``True`` or wrongly-``False`` flag self-heal within a
        day.

        The write is scoped to ``update_fields=["has_open_promises"]``, which
        popoto sends as an EVAL-only Lua call touching just that field and its
        index sets — no ``goal`` bytes are ever transmitted. This means a
        concurrent promise write (``add_promise`` / ``remove_promise`` /
        ``append_goal_version``, landing between this loop's re-fetch and its
        save) can never be clobbered by this method, structurally rather than
        by a narrowed timing window.

        Each row is re-fetched by both KeyFields immediately before deriving,
        so the derivation reads fresh ``goal`` data rather than a snapshot that
        may have gone stale during the enumeration. That re-fetch lives inside
        the per-row ``try`` on purpose: a raising ``query.get`` must cost only
        that one row, never abort the whole daily sweep. The residual staleness
        this leaves — the gap between the re-fetch and the save — can only ever
        produce a wrong flag, never lost data: every consumer
        (``at_rest_with_open_promises``) re-verifies against ``open_promises()``
        before surfacing anything, so a stale flag costs at most one wasted
        hydration or one delayed operator-surface signal.

        The loop assigns no ``_now()`` anywhere and never writes
        ``last_active_at``. That is deliberate: two machines running the daily
        tick concurrently against shared Redis must derive and write the same
        value for the same row, and any timestamp in the write would break
        that convergence.

        Idempotent, and writes only where the derived value disagrees with
        what is stored, so steady-state passes cost reads alone. Runs on the
        daily maintenance path via :meth:`repair_indexes`.

        Returns the number of Jobs restamped.
        """
        stamped = 0
        try:
            for job in cls.query.filter():
                try:
                    fresh = cls.query.get(id=job.id, room_id=job.room_id)
                    if fresh is None:
                        continue
                    derived = any(
                        entry.get("removed_ts") is None
                        for entry in fresh._goal_data().get("promises", [])
                    )
                    if fresh.has_open_promises is not derived:
                        fresh.has_open_promises = derived
                        # Field-scoped on purpose: see the docstring's write-scope
                        # invariant. Widening this list (e.g. adding
                        # last_active_at) would both reintroduce clobber risk and
                        # break cross-machine convergence.
                        fresh.save(update_fields=["has_open_promises"])
                        stamped += 1
                except Exception as e:  # noqa: BLE001 — one bad row never stops the backfill
                    logger.warning(
                        "[job] open-promise backfill failed for %s: %s",
                        getattr(job, "job_id", "?"),
                        e,
                    )
        except Exception as e:  # noqa: BLE001 — maintenance path never raises
            logger.warning("[job] open-promise backfill failed: %s", e)
        if stamped:
            logger.info("[job] open-promise backfill stamped %d Job(s)", stamped)
        return stamped
