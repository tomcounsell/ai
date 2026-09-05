"""Job — a responsibility to complete something end to end.

Schema (ratified one-shot by the Task 5 schema-gate ruling of
``docs/plans/durability-room-job-agentrun.md``):

- KeyField set = ``{id, room_id}`` — ``id`` (``AutoKeyField``) for identity;
  ``room_id`` (``KeyField``, composite ``{project_key}|{addressee}`` from
  :func:`models.room.room_id`) so a single recency
  ``SortedField(partition_by="room_id")`` serves the top-N bind-or-mint
  candidate lookup without an unbounded index.
- Two IndexedFields, both low-cardinality: ``status`` (``active`` /
  ``at-rest``) and ``has_open_expectations`` (two-valued bool). The schema
  gate's rule is a cardinality rule — never index a pid, uuid, or
  timestamp — and both honor it (Schema Gate Amendment 2,
  ``docs/plans/durability-room-job-agentrun.md``).
  ``has_open_expectations`` is a derived projection of ``goal``, never
  authoritative: every read that matters re-verifies with
  :meth:`Job.open_expectations`. ``status`` is chokepoint-maintained at
  ``_write_goal_data``: an open expectation forces ``active``, so the
  at-rest-with-open-expectation index intersection is an
  invariant-violation alarm, empty in steady state.
- ``goal`` is an **append-only-versioned plain field** (JSON). The router
  never model-authors it: at mint it holds only the mechanical placeholder
  (:func:`mint_placeholder_goal`), so ``goal`` is never null and the
  synchronous bind-or-mint path never blocks. Authoring the real goal (v1)
  is the PM's mandated first step on the Job.
- **Expectations are the single obligation primitive**, both directions,
  stored on the goal as ``(holder, owner, what, direction)`` entries.
  ``inbound``: what we owe a requester (the holder is the requester).
  ``outbound``: what a PM expects a spawned lane to deliver (the holder
  is the PM, the owner is the lane). Discharge appends ``removed_ts``
  rather than deleting, so the full obligation history stays
  reconstructable. A Job with an open expectation cannot be at rest.
- **Never hard-closed.** A Job goes to rest by age (``sweep_to_rest`` on the
  session-health cadence, threshold ``JOB_AT_REST_AGE_SECONDS``) and is
  revived by any new steering message regardless of age (``revive``). This
  revival-after-apparent-completion is user-visible behavior, documented in
  ``docs/features/durability-model.md``.
- Immortal: no ``Meta.ttl`` — the Job is the durable record of what was
  expected and delivered; it must outlive every session that served it.
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

# Age threshold after which an idle active Job goes to rest (rest-by-age,
# applied by ``Job.sweep_to_rest`` on the session-health cadence).
# GRAIN OF SALT: provisional/tunable via env — sized to keep multi-day
# conversations active across a weekend while letting abandoned threads
# reach rest within the week.
JOB_AT_REST_AGE_SECONDS = int(os.environ.get("JOB_AT_REST_AGE_SECONDS", str(72 * 3600)))

# Extra members the bounded recency read pulls beyond ``limit``, so a member
# whose backing hash has already gone (a transient orphan between a delete and
# the guarded index repair) is absorbed without an under-filled answer.
# GRAIN OF SALT: provisional/tunable via env — sized off the observed orphan
# rate, which is near zero outside the repair window.
JOB_RECENT_OVERFETCH = int(os.environ.get("JOB_RECENT_OVERFETCH", "5"))


def _now() -> datetime:
    return datetime.now(tz=UTC)


class CorruptGoalError(RuntimeError):
    """The stored ``goal`` bytes are not JSON, so no write may replace them.

    A ``goal`` that fails to decode is the one copy of that Job's obligation
    history. Every mutator (:meth:`Job.add_expectation`,
    :meth:`Job.discharge_expectation`, :meth:`Job.append_goal_version`) and
    the ``_write_goal_data`` chokepoint itself raise this rather than persist
    an empty structure over the original bytes. Reads stay tolerant so an
    unrelated caller never crashes on a corrupt row; writes fail closed so the
    corruption stays recoverable (issue #2862).
    """


# Sentinel returned by ``Job._parse_goal`` when the stored bytes do not decode.
_CORRUPT = object()

# Job ids whose corrupt goal this process has already sent to Sentry. The
# ERROR log fires on every read (it is the signal of record); the Sentry
# capture fires once per process per Job so the cadence readers (reconciler,
# health sweep) cannot flood one bad row into thousands of events.
_corrupt_goal_reported: set[str] = set()
_CORRUPT_GOAL_REPORT_CAP = 1000


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
    # Append-only-versioned goal + expectations, JSON (schema v2):
    #   {"versions": [{"ts", "author", "text"}, ...],
    #    "expectations": [{"id", "ts", "direction", "holder", "owner",
    #                      "what", "removed_ts", "placeholder"}, ...]}
    goal = Field(null=True)
    # Derived projection of `goal`, maintained at the _write_goal_data
    # chokepoint so it cannot be bypassed. Bounds the reconciler's scan to
    # an index instead of the whole Job population.
    # `type=bool` is load-bearing: without it the value hydrates as the
    # string "False", which is truthy.
    has_open_expectations = IndexedField(type=bool, default=False)

    # -- Persistence --------------------------------------------------------

    def save(
        self,
        pipeline=None,
        ignore_errors: bool = False,
        skip_auto_now: bool = False,
        update_fields: list | None = None,
        migrate_key: bool = False,
        **kwargs,
    ):
        """Re-attach UTC to a naive ``last_active_at`` before persisting.

        popoto 1.8.0 decodes a stored datetime without tzinfo, so a reloaded
        Job carries a naive ``last_active_at``; the next save would compute the
        SortedField score as ``naive.timestamp()`` — local time, skewed from the
        stored hash value by the host's UTC offset. Every write funnels through
        here, so this one re-attach makes every score a pure UTC epoch.

        **Instant-preserving, not a re-stamp.** It attaches the tzinfo the value
        already meant and never assigns ``_now()``: re-stamping would refresh
        recency on every unrelated write, resurrecting idle Jobs and defeating
        rest-by-age.  Idempotent — an aware value is left untouched.

        The reattach is gated on the field being in scope. A scoped save that
        excludes ``last_active_at`` (``backfill_open_expectations_index``'s
        ``save(update_fields=["has_open_expectations"])``, whose docstring
        guarantees it never writes recency) must not touch the SortedField score
        path at all; a scoped save that *names* the field still reattaches.

        The signature mirrors popoto 1.8.0's ``Model.save`` exactly (rather
        than ``*args``) so a caller passing ``update_fields`` positionally is
        still captured by the guard — a splat signature would let a positional
        ``update_fields`` slip past the keyword check and only surface as a
        ``TypeError`` at the ``super().save`` delegation.
        """
        if update_fields is None or "last_active_at" in update_fields:
            value = self.last_active_at
            if isinstance(value, datetime) and value.tzinfo is None:
                self.last_active_at = value.replace(tzinfo=UTC)
        return super().save(
            pipeline=pipeline,
            ignore_errors=ignore_errors,
            skip_auto_now=skip_auto_now,
            update_fields=update_fields,
            migrate_key=migrate_key,
            **kwargs,
        )

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
                "expectations": [],
            },
            save=False,
        )
        job.save()
        return job

    # -- Goal (append-only versioned) ---------------------------------------

    def _parse_goal(self):
        """Decode the stored ``goal`` bytes, or return :data:`_CORRUPT`.

        Two categorically different failures share this field and must not
        share a handler. A null/empty field or a wrong-shaped JSON value is
        something this system's own writer can plausibly leave behind, and
        ``_goal_data`` coerces those to empty. Bytes that do not decode at all
        (or a non-string value) were not written intact by this system; that
        is corruption, and the caller decides how loudly to treat it.
        """
        if not self.goal:
            return {}
        try:
            return json.loads(self.goal)
        except (json.JSONDecodeError, TypeError):
            return _CORRUPT

    def goal_is_corrupt(self) -> bool:
        """True when the stored ``goal`` bytes do not decode as JSON.

        A pure predicate (no logging, no Sentry) so scan helpers and the
        maintenance path can branch on it without re-reporting.
        """
        return self._parse_goal() is _CORRUPT

    def _report_corrupt_goal(self) -> None:
        """ERROR log on every call; one Sentry event per process per Job."""
        raw = self.goal
        preview = repr(raw)[:80]
        logger.error(
            "[job] CORRUPT goal on %s (room=%s): stored value does not decode as "
            "JSON (%s, %d chars); reading as empty and refusing every write until "
            "repaired. Preview: %s",
            self.job_id,
            self.room_id,
            type(raw).__name__,
            len(str(raw)),
            preview,
        )
        if self.job_id in _corrupt_goal_reported:
            return
        if len(_corrupt_goal_reported) < _CORRUPT_GOAL_REPORT_CAP:
            _corrupt_goal_reported.add(self.job_id)
        try:
            import sentry_sdk

            sentry_sdk.capture_message(
                f"[job] corrupt goal JSON on Job {self.job_id} (room={self.room_id}); "
                "writes refused until repaired",
                level="error",
            )
        except Exception:  # noqa: BLE001 — the ERROR log above is the signal of record
            logger.warning("[job] Sentry capture for corrupt goal failed", exc_info=True)

    def _goal_data(self) -> dict:
        """Tolerant read: every caller gets a well-shaped dict.

        Corruption reads as empty so an unrelated caller never crashes on a
        bad row, but it is never quiet: :meth:`_report_corrupt_goal` fires,
        and every write path refuses until the bytes are repaired.
        """
        data = self._parse_goal()
        if data is _CORRUPT:
            self._report_corrupt_goal()
            data = {}
        if not isinstance(data, dict):
            logger.warning("[job] non-object goal JSON on %s; treating as empty", self.job_id)
            data = {}
        # Coerce rather than setdefault: a stored null (or any non-list) must
        # read as empty, not blow up every caller downstream.
        for key in ("versions", "expectations"):
            if not isinstance(data.get(key), list):
                data[key] = []
        # Merge-on-every-read self-heal (Race 4): a lingering pre-cutover
        # `promises` key — including one written by an unrestarted old-code
        # process AFTER the offline migration ran — is absorbed here as
        # inbound expectation entries, id-deduplicated, history intact.
        # Deliberately not a one-shot: every read converges the two shapes.
        legacy = data.pop("promises", None)
        if legacy:
            existing_ids = {e.get("id") for e in data["expectations"]}
            for entry in legacy:
                if entry.get("id") in existing_ids:
                    continue
                data["expectations"].append(
                    {
                        "id": entry.get("id"),
                        "ts": entry.get("ts"),
                        "direction": "inbound",
                        "holder": "requester",
                        "owner": "pm",
                        "what": entry.get("text", ""),
                        "removed_ts": entry.get("removed_ts"),
                    }
                )
        return data

    def _mutable_goal_data(self) -> dict:
        """The read half of every read-modify-write; refuses on corruption.

        ``_goal_data`` reads a corrupt goal as empty. Feeding that empty dict
        back through ``_write_goal_data`` would persist ``{"versions": [],
        "expectations": []}`` over the only copy of the original bytes, so a
        mutator must never start from it. Raising here (before any mutation)
        also makes :meth:`discharge_expectation` loud instead of a misleading
        ``False``.
        """
        if self.goal_is_corrupt():
            self._report_corrupt_goal()
            raise CorruptGoalError(
                f"job {self.job_id}: stored goal is not JSON; refusing to mutate it "
                "(the stored bytes are the only copy of its history)"
            )
        return self._goal_data()

    def _write_goal_data(self, data: dict, *, save: bool = True) -> None:
        # Fail closed on corruption at the chokepoint itself, so no caller
        # (including a migration handing back a freshly read dict) can turn a
        # truncated-but-present goal into a clean, empty, plausible one.
        if self.goal_is_corrupt():
            self._report_corrupt_goal()
            raise CorruptGoalError(
                f"job {self.job_id}: stored goal is not JSON; refusing to overwrite it"
            )
        # Derive BEFORE assigning goal so a derivation failure on malformed
        # entries raises without half-writing the record.
        has_open = any(
            isinstance(entry, dict) and entry.get("removed_ts") is None
            for entry in data.get("expectations", [])
        )
        self.goal = json.dumps(data)
        # Every expectation mutation funnels through here (mint,
        # add_expectation, discharge_expectation, append_goal_version), so
        # deriving the index flag AND the status projection at this point
        # makes them un-bypassable rather than a discipline callers have to
        # remember. A single write site computes both, so they cannot
        # disagree: an open expectation forces `active`.
        self.has_open_expectations = has_open
        if has_open and self.status != "active":
            self.status = "active"
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
        data = self._mutable_goal_data()
        data["versions"].append({"ts": _now().isoformat(), "author": author, "text": text})
        self._write_goal_data(data)

    # -- Expectations (the single obligation primitive, both directions) ----

    def add_expectation(
        self,
        what: str,
        *,
        direction: str = "inbound",
        owner: str | None = None,
        holder: str | None = None,
        placeholder: bool = False,
    ) -> str:
        """Append an open expectation entry; returns its id.

        ``inbound`` (the default): what we owe a requester — the requester
        holds it, we own it, so ``holder``/``owner`` default to
        ``requester``/``pm``. ``outbound``: what a PM expects a spawned lane
        to deliver — ``holder`` defaults to ``pm`` and ``owner`` (the lane's
        session id or slug) must be named. ``placeholder=True`` marks a
        mechanical null-fallback entry (spawn chokepoint) the PM is nudged
        to refine — provenance-derived, mirroring :meth:`goal_is_placeholder`.

        An expectation with an empty ``owner`` or ``what`` is rejected
        loudly: an unownable expectation is unreconcilable — worse than
        none at all.
        """
        if direction not in ("inbound", "outbound"):
            raise ValueError(f"expectation direction must be inbound|outbound, got {direction!r}")
        if not what or not str(what).strip():
            raise ValueError("expectation 'what' must be non-empty")
        if owner is None and direction == "inbound":
            owner = "pm"
        if not owner or not str(owner).strip():
            raise ValueError("expectation 'owner' must be non-empty (who must deliver this?)")
        if holder is None:
            holder = "requester" if direction == "inbound" else "pm"
        data = self._mutable_goal_data()
        expectation_id = uuid.uuid4().hex[:12]
        data["expectations"].append(
            {
                "id": expectation_id,
                "ts": _now().isoformat(),
                "direction": direction,
                "holder": holder,
                "owner": str(owner).strip(),
                "what": str(what).strip(),
                "removed_ts": None,
                "placeholder": bool(placeholder),
            }
        )
        self._write_goal_data(data)
        return expectation_id

    def discharge_expectation(self, expectation_id: str) -> bool:
        """Discharge an expectation. Append-only: stamps ``removed_ts``, keeps the entry.

        Always owner-authored — no mechanical trigger ever discharges; the
        reconciler surfaces evidence and the PM discharges deliberately.
        """
        data = self._mutable_goal_data()
        for entry in data["expectations"]:
            if entry.get("id") == expectation_id and entry.get("removed_ts") is None:
                entry["removed_ts"] = _now().isoformat()
                self._write_goal_data(data)
                return True
        return False

    def open_expectations(self, *, direction: str | None = None) -> list[dict]:
        """Open (undischarged) expectations, optionally filtered by direction."""
        entries = [e for e in self._goal_data()["expectations"] if e.get("removed_ts") is None]
        if direction is not None:
            entries = [e for e in entries if e.get("direction") == direction]
        return entries

    def all_expectations(self) -> list[dict]:
        return list(self._goal_data()["expectations"])

    # -- Lifecycle (rest by age, revived by any steer; never hard-closed) ---

    def touch(self) -> None:
        """Record activity (message bound, PM turn) — refreshes recency.

        Field-scoped save (the structural clobber-proof idiom): a bare
        ``save()`` would serialize the whole hash, including a concurrent
        writer's in-flight ``goal`` mutation loaded earlier by this
        instance. ``update_fields=["last_active_at"]`` writes only what this
        method actually mutates.
        """
        self.last_active_at = _now()
        self.save(update_fields=["last_active_at"])

    def mark_at_rest(self) -> None:
        """Field-scoped save (the structural clobber-proof idiom): mutates
        only ``status``, so an in-flight ``goal`` write from a concurrent
        expectation mutation is never clobbered. Also deliberately excludes
        ``last_active_at`` — resting a Job by age must never refresh its
        recency.
        """
        self.status = "at-rest"
        self.save(update_fields=["status"])

    def revive(self) -> None:
        """Any steering message revives a Job regardless of age.

        Field-scoped save (the structural clobber-proof idiom): mutates only
        ``status`` and ``last_active_at``, so an in-flight ``goal`` write
        from a concurrent expectation mutation is never clobbered.
        """
        self.status = "active"
        self.last_active_at = _now()
        self.save(update_fields=["status", "last_active_at"])

    # -- Queries ------------------------------------------------------------

    @classmethod
    def recent_for_room(cls, room_id: str, *, limit: int = 5) -> list[Job]:
        """Top-N most recent Jobs in a Room, newest first.

        A bounded reverse-range read over the ``last_active_at`` SortedField's
        per-Room partition: one ``ZREVRANGE`` for the top members, then one
        pipelined hydration of just those members. Cost is a function of
        ``limit``, not of the Room's lifetime Job count — popoto 1.8.0's
        ``QueryBuilder`` has no early-limit path for a SortedField, so a
        ``filter()`` here would hydrate every Job in the Room (twice, per
        popoto#2639) to answer a top-5 question. This runs on the bind-or-mint
        hot path for every routed inbound message.

        The partition key is **derived, never hand-built**: ``DB_key.clean()``
        escapes ``:`` and ``/``, and every real ``room_id`` contains a colon,
        so an f-string would silently read a key that does not exist.

        The read over-fetches by ``JOB_RECENT_OVERFETCH`` so a member whose
        backing hash is already gone (dropped by ``skip_none``) does not
        under-fill the answer. An under-filled result is never re-fetched: it
        means the Room genuinely has fewer live Jobs, or an index repair is
        mid-flight — the same fail-open posture as every other step here.

        Fail-open contract (unchanged): any failure logs a warning and returns
        ``[]``; the caller treats that as "no candidates" and mints.
        """
        if limit <= 0:
            return []
        try:
            from popoto.redis_db import POPOTO_REDIS_DB

            partition_key = SortedField.get_sortedset_db_key(
                cls, "last_active_at", room_id
            ).redis_key
            fetch_n = limit + JOB_RECENT_OVERFETCH
            members = POPOTO_REDIS_DB.zrevrange(partition_key, 0, fetch_n - 1)
            redis_keys = [m.decode() if isinstance(m, bytes) else str(m) for m in members]
            jobs = cls.query.get_many(redis_keys, skip_none=True)
        except Exception as e:  # noqa: BLE001 — candidate lookup must fail open
            logger.warning("[job] recent_for_room failed for %s: %s", room_id, e)
            return []
        return list(jobs)[:limit]

    @classmethod
    def sweep_to_rest(cls, now: float | None = None) -> int:
        """Rest-by-age: transition idle active Jobs to ``at-rest`` via the ORM.

        Runs on the session-health cadence (invoked by
        ``agent/session_health.py::_check_jobs_at_rest_with_open_expectations``
        immediately before the invariant-alarm scan, so the backstop always
        evaluates fresh rest state — never correct logic over empty input).
        An active Job whose ``last_active_at`` is older than
        ``JOB_AT_REST_AGE_SECONDS`` goes to rest through ``mark_at_rest()``
        (a normal ORM save, so INDEX_SWAP_LUA moves the ``status`` index
        membership). Rest is never terminal — any reply revives the Job.

        A Job with an OPEN expectation never rests: an obligation is
        outstanding, so the Job is not finished no matter how idle. An
        expectation-less idle Job still rests by age — under-recording
        degrades to rest-by-age (unknown surfaced by time), never to a
        false "done" (hazard 1).

        Returns the number of Jobs transitioned.
        """
        rested = 0
        try:
            from utils.utc import to_unix_ts

            now_ts = now if now is not None else time.time()
            cutoff = now_ts - JOB_AT_REST_AGE_SECONDS
            for job in cls.query.filter(status="active"):
                try:
                    # A corrupt goal cannot prove its obligations are met, so
                    # the Job stays active (pinned visible) until repaired.
                    if job.open_expectations() or job.goal_is_corrupt():
                        continue
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
    def at_rest_with_open_expectations(cls) -> list[Job]:
        """Jobs at rest that still carry an open expectation entry.

        **Invariant-violation alarm**: ``status`` is chokepoint-maintained
        (an open expectation forces ``active`` at ``_write_goal_data``) and
        ``sweep_to_rest`` skips Jobs with open expectations, so in steady
        state this intersection is EMPTY. Any hit means drift or a
        migration edge and is surfaced to the operator surface only —
        never to human chat.

        Served by intersecting the ``status`` and ``has_open_expectations``
        index sets, so the work is proportional to the flagged set rather
        than to the at-rest population.

        ``has_open_expectations`` is a derived projection, so each candidate
        is re-verified against :meth:`open_expectations` — the ``goal`` JSON
        stays the single source of truth, and a stale flag can only cost a
        hydration, never a wrong answer.
        """
        flagged = []
        try:
            for job in cls.query.filter(status="at-rest", has_open_expectations=True):
                if job.open_expectations() or job.goal_is_corrupt():
                    flagged.append(job)
        except Exception as e:  # noqa: BLE001 — a backstop query never raises
            logger.warning("[job] at_rest_with_open_expectations query failed: %s", e)
        return flagged

    @classmethod
    def with_open_expectations(cls) -> list[Job]:
        """All Jobs carrying an open expectation — the reconciler's scan root.

        Index-bounded via ``has_open_expectations``; each candidate is
        re-verified against :meth:`open_expectations` (stale flag costs a
        hydration, never a wrong answer).
        """
        flagged = []
        try:
            for job in cls.query.filter(has_open_expectations=True):
                if job.open_expectations() or job.goal_is_corrupt():
                    flagged.append(job)
        except Exception as e:  # noqa: BLE001 — a scan helper never raises
            logger.warning("[job] with_open_expectations query failed: %s", e)
        return flagged

    # -- Guarded index repair (Risk 2 / #2207) ------------------------------

    _repair_lock = threading.Lock()

    @classmethod
    def repair_indexes(cls) -> tuple[int, int]:
        """Guarded repair path — the ONLY sanctioned index rebuild for Job.

        Job is listed in ``scripts/popoto_index_cleanup._GUARDED_ELSEWHERE``
        so the generic raw ``rebuild_indexes()`` sweep never touches it, and
        registered in that module's ``_run_guarded_repairs()`` so this method
        is what the daily cleanup reflection invokes instead. Both halves are
        required: the exclusion alone left Job with no index hygiene at all
        between the model shipping and issue #2640.

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
            # rebuild_indexes() re-scores every row via field.on_save on
            # naive-decoded instances — naive.timestamp() is local time, and
            # the rebuild bypasses save()'s UTC-reattach — so on a non-UTC
            # host the rebuild itself re-skews every recency score. Sweep the
            # scores back so the maintenance path is score-preserving.
            scanned, renormalized = cls.renormalize_last_active_scores()
            if renormalized:
                logger.info(
                    "[job] re-normalized %d of %d recency score(s) after the index rebuild",
                    renormalized,
                    scanned,
                )
            cls.backfill_open_expectations_index()
            return (quarantined, rebuilt if isinstance(rebuilt, int) else 0)
        finally:
            cls._repair_lock.release()

    # Rows per pipeline in :meth:`renormalize_last_active_scores`. Sized on the
    # mechanism, never on a measured population: each batch is one HMGET
    # pipeline (three small fields per row) plus one ZSCORE pipeline, so 500
    # bounds both the reply payload and the per-batch memory at a few tens of
    # kilobytes regardless of how many Jobs exist. It also serves as the SSCAN
    # COUNT hint; SSCAN treats COUNT as advisory (a small, listpack-encoded set
    # comes back whole), so members are re-chunked client-side to this size
    # before any pipeline is built.
    _RENORMALIZE_BATCH_SIZE = 500

    @classmethod
    def renormalize_last_active_scores(
        cls, *, batch_size: int = _RENORMALIZE_BATCH_SIZE
    ) -> tuple[int, int]:
        """Sweep every recency score back to the pure UTC epoch its hash implies.

        The single shared implementation behind two callers:

        - the one-shot ``backfill_job_last_active_scores`` migration
          (``scripts/update/migrations.py``), which sweeps skew written
          before the :meth:`save` UTC-reattach override shipped; and
        - :meth:`repair_indexes`, because popoto's ``rebuild_indexes()``
          re-scores every row via ``field.on_save`` on naive-decoded
          instances — ``naive.timestamp()`` is local time, bypassing the
          :meth:`save` override entirely — so on a non-UTC host every
          rebuild (run at worker startup via
          ``scripts/popoto_index_cleanup.run_cleanup``) would re-skew every
          score the migration repaired.

        Cursored and pipelined (issue #2848). The class set is walked with
        ``SSCAN`` and re-chunked into ``batch_size`` rows. Each chunk costs
        two round trips: one pipeline of ``HMGET id room_id last_active_at``
        (decoded with popoto's own hash decoder, so no Job is hydrated) and
        one pipeline of ``ZSCORE`` against each row's Room partition (key
        **derived** via ``SortedField.get_sortedset_db_key``, never
        hand-built). Startup cost is therefore ``O(N / batch_size)`` round
        trips with per-batch memory independent of ``N``; Job is immortal
        (no ``Meta.ttl``), so that bound is what keeps a growing population
        off the worker-start critical path. ``SSCAN`` may hand back a member
        twice if the set is rewritten mid-walk; the sweep is idempotent, so a
        repeat costs one extra comparison and can only inflate ``scanned``.

        A row whose score sits outside a 1-second tolerance is re-read fresh
        and repaired with the structural clobber-proof idiom
        ``fresh.save(update_fields=["last_active_at"])`` — a field-scoped
        write that can never touch ``goal``/``status``, and one that names
        the field so the :meth:`save` tz-reattach fires. Repairs are the only
        per-row writes and the only per-row round trips. **Instant-
        preserving**: each row keeps its own stored instant; no constant
        timestamp is ever stamped (spike-4 tie-break hazard). A Job whose
        partition member is absent, or whose instant is unreadable, is
        skipped — partition membership belongs to the rebuild, not this
        sweep. Idempotent (a repaired score is inside tolerance next pass)
        and failure tolerant at two grains: one bad row logs and the chunk
        continues; one failed pipeline logs and the walk continues with the
        next chunk.

        Returns ``(scanned, repaired)``. ``(0, 0)`` is overloaded: it is also
        the return when the enumeration itself fails before any row is seen
        (Redis down) — the guard logs a WARNING and swallows the error so
        :meth:`repair_indexes` still reaches
        :meth:`backfill_open_expectations_index`. An ``SSCAN`` failure part
        way through returns the counts accumulated so far.
        """
        from popoto.redis_db import POPOTO_REDIS_DB

        class_set_key = cls._meta.db_class_set_key.redis_key
        scanned = 0
        repaired = 0
        cursor = 0
        while True:
            try:
                cursor, members = POPOTO_REDIS_DB.sscan(
                    class_set_key, cursor=cursor, count=batch_size
                )
            except Exception as e:  # noqa: BLE001 — maintenance path never raises
                logger.warning(
                    "[job] score renormalization SKIPPED -- enumeration failed "
                    "after %d row(s) %s: %s",
                    scanned,
                    type(e).__name__,
                    e,
                )
                return (scanned, repaired)
            keys = [m.decode() if isinstance(m, bytes) else str(m) for m in members]
            for start in range(0, len(keys), batch_size):
                chunk_scanned, chunk_repaired = cls._renormalize_score_chunk(
                    keys[start : start + batch_size]
                )
                scanned += chunk_scanned
                repaired += chunk_repaired
            if cursor == 0:
                break
        logger.info(
            "[job] renormalized recency scores across %d Job(s), %d repaired",
            scanned,
            repaired,
        )
        return (scanned, repaired)

    _RENORMALIZE_FIELDS = ("id", "room_id", "last_active_at")

    @classmethod
    def _renormalize_score_chunk(cls, keys: list[str]) -> tuple[int, int]:
        """One batch of :meth:`renormalize_last_active_scores`: two pipelines, then repairs.

        Reads only the three fields the comparison needs, decoded through
        popoto's ``decode_popoto_model_hashmap(fields_only=True)`` so the
        bytes on the wire mean exactly what a hydrated Job would carry. A key
        whose hash is gone (class-set member outliving its row) decodes to
        nothing and is skipped; the rebuild owns class-set hygiene.

        Returns ``(scanned, repaired)`` for this chunk. A failed pipeline
        returns ``(0, 0)`` after a WARNING so the caller moves on.
        """
        from popoto.models.encoding import decode_popoto_model_hashmap
        from popoto.redis_db import POPOTO_REDIS_DB

        from utils.utc import to_unix_ts

        field_names = list(cls._RENORMALIZE_FIELDS)
        try:
            pipe = POPOTO_REDIS_DB.pipeline(transaction=False)
            for key in keys:
                pipe.hmget(key, field_names)
            raw_rows = pipe.execute()
        except Exception as e:  # noqa: BLE001 — one failed batch never stops the sweep
            logger.warning(
                "[job] score renormalization SKIP batch of %d -- hmget pipeline %s: %s",
                len(keys),
                type(e).__name__,
                e,
            )
            return (0, 0)

        rows: list[tuple[str, dict]] = []
        for key, values in zip(keys, raw_rows, strict=True):
            redis_hash = {
                name.encode(): value
                for name, value in zip(field_names, values, strict=True)
                if value is not None
            }
            decoded = decode_popoto_model_hashmap(cls, redis_hash, fields_only=True)
            if not decoded:
                continue
            fields = {(k.decode() if isinstance(k, bytes) else k): v for k, v in decoded.items()}
            if fields.get("room_id") is None or fields.get("id") is None:
                continue
            rows.append((key, fields))
        if not rows:
            return (0, 0)

        try:
            pipe = POPOTO_REDIS_DB.pipeline(transaction=False)
            for key, fields in rows:
                partition_key = SortedField.get_sortedset_db_key(
                    cls, "last_active_at", fields["room_id"]
                ).redis_key
                pipe.zscore(partition_key, key)
            scores = pipe.execute()
        except Exception as e:  # noqa: BLE001 — one failed batch never stops the sweep
            logger.warning(
                "[job] score renormalization SKIP batch of %d -- zscore pipeline %s: %s",
                len(rows),
                type(e).__name__,
                e,
            )
            return (0, 0)

        repaired = 0
        for (key, fields), score in zip(rows, scores, strict=True):
            try:
                expected = to_unix_ts(fields.get("last_active_at"))
                if score is None or expected is None:
                    continue
                if abs(float(score) - expected) <= 1.0:
                    continue
                fresh = cls.query.get(id=fields["id"], room_id=fields["room_id"])
                if fresh is None:
                    continue
                fresh.save(update_fields=["last_active_at"])
                repaired += 1
            except Exception as e:  # noqa: BLE001 — one bad row never stops the sweep
                logger.warning(
                    "[job] score renormalization SKIP job=%s -- %s: %s",
                    fields.get("id", "?"),
                    type(e).__name__,
                    e,
                )
        return (len(rows), repaired)

    @classmethod
    def backfill_open_expectations_index(cls) -> int:
        """Stamp ``has_open_expectations`` on Jobs whose stored flag disagrees with ``goal``.

        This is a daily re-derivation, not a one-shot legacy migration:
        ``rebuild_indexes()`` runs immediately before this method on the same
        maintenance path (see :meth:`repair_indexes`) and already stamps every
        hash — including legacy rows with no ``has_open_expectations`` attribute —
        via ``model_class(**model_attrs)``, which fills the missing field from
        ``default=False`` and calls ``on_save`` for every field. So by the time
        this loop runs, every row already has a flag; what this loop catches is
        any row whose flag has since drifted from its ``goal`` for any reason,
        making a wrongly-``True`` or wrongly-``False`` flag self-heal within a
        day.

        The write is scoped to ``update_fields=["has_open_expectations"]``, which
        popoto sends as an EVAL-only Lua call touching just that field and its
        index sets — no ``goal`` bytes are ever transmitted. This means a
        concurrent expectation write (``add_expectation`` /
        ``discharge_expectation`` / ``append_goal_version``, landing between
        this loop's re-fetch and its save) can never be clobbered by this
        method, structurally rather than by a narrowed timing window.

        Each row is re-fetched by both KeyFields immediately before deriving,
        so the derivation reads fresh ``goal`` data rather than a snapshot that
        may have gone stale during the enumeration. That re-fetch lives inside
        the per-row ``try`` on purpose: a raising ``query.get`` must cost only
        that one row, never abort the whole daily sweep. The residual staleness
        this leaves — the gap between the re-fetch and the save — can only ever
        produce a wrong flag, never lost data: every consumer
        (``at_rest_with_open_expectations``, ``with_open_expectations``)
        re-verifies against ``open_expectations()`` before surfacing anything,
        so a stale flag costs at most one wasted hydration or one delayed
        operator-surface signal.

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
                    if fresh.goal_is_corrupt():
                        # The stored flag is the last known truth; an empty
                        # parse cannot disprove it. Re-deriving here would drop
                        # the Job out of the reconciler's index (#2862).
                        fresh._report_corrupt_goal()
                        continue
                    derived = any(
                        entry.get("removed_ts") is None
                        for entry in fresh._goal_data().get("expectations", [])
                    )
                    if fresh.has_open_expectations is not derived:
                        fresh.has_open_expectations = derived
                        # Field-scoped on purpose: see the docstring's write-scope
                        # invariant. Widening this list (e.g. adding
                        # last_active_at) would both reintroduce clobber risk and
                        # break cross-machine convergence.
                        fresh.save(update_fields=["has_open_expectations"])
                        stamped += 1
                except Exception as e:  # noqa: BLE001 — one bad row never stops the backfill
                    logger.warning(
                        "[job] open-expectation backfill failed for %s: %s",
                        getattr(job, "job_id", "?"),
                        e,
                    )
        except Exception as e:  # noqa: BLE001 — maintenance path never raises
            logger.warning("[job] open-expectation backfill failed: %s", e)
        if stamped:
            logger.info("[job] open-expectation backfill stamped %d Job(s)", stamped)
        return stamped
