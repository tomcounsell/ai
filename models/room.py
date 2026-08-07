"""Room — the environment a conversation happens in.

``Room = (project_key, addressee)`` where ``addressee`` is one of
``telegram:{chat_id}`` | ``email:{address}`` | ``system``. Resolved from
``projects.json`` (which stays the source of truth — Room caches the resolved
binding, it does not replace the config). Owns addressing and the durable
inbox. Immortal: no ``Meta.ttl``.

Schema (ratified one-shot by the Task 5 schema-gate ruling of
``docs/plans/durability-room-job-agentrun.md``):

- KeyField set = ``{project_key, addressee}`` — every Room resolution is a
  direct composite-primary-key lookup.
- **No IndexedField.** This is load-bearing for Risk 2 (#2207): the phantom
  index flood mechanism is an ``IndexedField.on_save`` re-SADDing values
  decoded off identity-less hashes during ``rebuild_indexes()``. With zero
  IndexedFields Room has no ``$IndexF`` surface at all; the guarded
  :meth:`Room.repair_indexes` below closes the remaining class-set/KeyField
  leg by quarantining identity-less hashes before any rebuild.
- The inbox is a Redis list under the Room's primary key (a ``::inbox``
  companion key — the ``::`` suffix keeps it out of index-drift hash counts),
  drained by pattern-scanning consumers, never ``.filter()``ed.

Every session belongs to a Room (spike-1): chatless sessions get the
per-project ``system`` Room, unifying the three prior synthetic addressees
(``chat_id="0"``, ``chat_id = session_id``, default-DM inheritance).
"""

from __future__ import annotations

import json
import logging
import os
import threading

from popoto import Field, KeyField, Model

from utils.peer import (
    _numeric_peer,  # used below at _numeric_peer(raw)
    deliverable_telegram_peer,  # noqa: F401 (re-exported for back-compat)
)

logger = logging.getLogger(__name__)

SYSTEM_ADDRESSEE = "system"

# Cap on retained Room-inbox entries during the shadow phase (Task 11 phase 1:
# the durable append is written alongside the untouched dispatch flow and
# nothing drains it yet, so an uncapped list would grow without bound).
# GRAIN OF SALT: provisional/tunable via env; sized to hold days of traffic in
# the busiest room while staying trivially small for Redis.
ROOM_INBOX_MAX_ENTRIES = int(os.environ.get("ROOM_INBOX_MAX_ENTRIES", "1000"))


def telegram_addressee(chat_id) -> str:
    """Addressee for a Telegram chat — DMs and groups identically."""
    return f"telegram:{chat_id}"


def email_addressee(address: str) -> str:
    """Addressee for an email correspondent (case-insensitive address)."""
    return f"email:{address.strip().lower()}"


def room_id(project_key: str, addressee: str) -> str:
    """Composite room id ``{project_key}|{addressee}`` (Job.room_id shape)."""
    return f"{project_key}|{addressee}"


def addressee_for_session(session) -> str:
    """Map an AgentSession onto its Room addressee.

    - Numeric ``chat_id`` (Telegram DM, group, or supergroup) → ``telegram:``.
    - ``chat_id`` containing ``@`` (the email bridge stores the sender address
      as ``chat_id``) → ``email:``.
    - Anything else (no chat, ``chat_id = session_id`` synthetics, reflection
      sessions' placeholder ``chat_id="0"``) → the per-project ``system``
      Room. Zero is not a valid Telegram peer (the relay's zero-guard drops
      it), so the placeholder is a chatless marker, not an address (#2497).
    """
    chat_id = getattr(session, "chat_id", None)
    if chat_id:
        raw = str(chat_id).strip()
        numeric = _numeric_peer(raw)
        if numeric is not None:
            if numeric == 0:
                return SYSTEM_ADDRESSEE
            return telegram_addressee(raw)
        # numeric is None (non-numeric or "--5"-style garbage) — fall through
        # to the email check rather than short-circuiting to SYSTEM_ADDRESSEE,
        # or every email-bridge session would silently re-home to the system
        # Room.
        if "@" in raw:
            return email_addressee(raw)
    return SYSTEM_ADDRESSEE


def room_id_for_session(session) -> str | None:
    """Composite room id for a session, or ``None`` without a project_key."""
    project_key = getattr(session, "project_key", None)
    if not project_key:
        return None
    return room_id(str(project_key), addressee_for_session(session))


class Room(Model):
    """The environment a conversation happens in. Immortal."""

    project_key = KeyField()
    addressee = KeyField()
    # Plain metadata (not queryable — read off a fetched Room only).
    display_name = Field(null=True)

    # -- Resolution ---------------------------------------------------------

    @classmethod
    def resolve(
        cls,
        project_key: str,
        addressee: str,
        display_name: str | None = None,
    ) -> Room:
        """Get or create the Room for ``(project_key, addressee)``.

        Idempotent: the KeyField pair is the primary key, so a concurrent
        double-create converges on the same record. ``display_name`` is
        refreshed opportunistically when provided.
        """
        existing = cls.query.filter(project_key=project_key, addressee=addressee)
        if existing:
            room = existing[0]
            if display_name and room.display_name != display_name:
                room.display_name = display_name
                room.save()
            return room
        room = cls(project_key=project_key, addressee=addressee, display_name=display_name)
        room.save()
        return room

    # -- Identity -----------------------------------------------------------

    @property
    def room_id(self) -> str:
        return room_id(self.project_key, self.addressee)

    # -- Inbox (durable, Room-owned) ----------------------------------------

    @property
    def inbox_key(self) -> str:
        """Companion list key under the Room's primary key.

        The ``::`` suffix follows the repo's companion-key convention, so
        index-drift hash counting (``agent/index_drift.py``) excludes it.
        """
        return f"{self.db_key.redis_key}::inbox"

    def append_inbox(self, entry: dict) -> bool:
        """Durably append one inbox entry (RPUSH + cap trim). Never raises.

        This is the single-operation loss window of the target inbound flow
        (👀 → durable append → route → bind). A failure returns ``False`` and
        logs at ERROR — loud, but it must never take down the live intake
        path it shadows.
        """
        from popoto.redis_db import POPOTO_REDIS_DB

        try:
            payload = json.dumps(entry)
            key = self.inbox_key
            POPOTO_REDIS_DB.rpush(key, payload)
            POPOTO_REDIS_DB.ltrim(key, -ROOM_INBOX_MAX_ENTRIES, -1)
            return True
        except Exception as e:  # noqa: BLE001 — intake must survive an inbox outage
            logger.error("[room] inbox append failed for %s: %s", self.room_id, e)
            return False

    def peek_inbox(self, limit: int | None = None) -> list[dict]:
        """Non-destructive FIFO read of inbox entries (newest last)."""
        from popoto.redis_db import POPOTO_REDIS_DB

        start = -limit if limit else 0
        entries: list[dict] = []
        try:
            for raw in POPOTO_REDIS_DB.lrange(self.inbox_key, start, -1):
                try:
                    entries.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    logger.warning("[room] invalid inbox JSON in %s: %r", self.inbox_key, raw)
        except Exception as e:  # noqa: BLE001
            logger.error("[room] inbox peek failed for %s: %s", self.room_id, e)
        return entries

    def inbox_len(self) -> int:
        from popoto.redis_db import POPOTO_REDIS_DB

        try:
            return int(POPOTO_REDIS_DB.llen(self.inbox_key))
        except Exception:  # noqa: BLE001
            return 0

    def clear_inbox(self) -> None:
        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.delete(self.inbox_key)

    # -- Guarded index repair (Risk 2 / #2207) ------------------------------

    _repair_lock = threading.Lock()

    @classmethod
    def repair_indexes(cls) -> tuple[int, int]:
        """Guarded repair path — the ONLY sanctioned index rebuild for Room.

        Room is listed in ``scripts/popoto_index_cleanup._GUARDED_ELSEWHERE``
        so the generic raw ``rebuild_indexes()`` sweep never touches it; this
        method is what the daily cleanup reflection invokes instead.

        Guard: **DELETES** identity-less ``Room:*`` hashes — records missing
        either KeyField — BEFORE calling ``rebuild_indexes()``, so a phantom
        hash can never be re-indexed into the class set on every rebuild (the
        #2207 flood mechanism, adapted to a model with zero IndexedFields:
        Room has no ``$IndexF`` surface, the class-set/KeyField leg is the
        only one to guard). Note the blast-radius difference from
        ``AgentSession.repair_indexes()``: that guard merely *skips* the
        re-SADD for identity-less records and leaves the hash in place; this
        one removes the hash itself. Safe here because a Room hash without
        both KeyFields is unreachable through the ORM by construction (the
        KeyField pair IS the primary key) and Room carries no other payload
        worth preserving forensically.

        Returns ``(quarantined_count, rebuilt_count)`` — same arity as
        ``AgentSession.repair_indexes()``.
        """
        from popoto.redis_db import POPOTO_REDIS_DB

        from config.popoto_floor import assert_popoto_floor

        # Below-floor popoto must never reach the destructive rebuild
        # (see config/popoto_floor.py / issue #2536).
        assert_popoto_floor()

        if not cls._repair_lock.acquire(blocking=False):
            logger.warning("[room] repair_indexes already running; skipping this invocation")
            return (0, 0)
        try:
            quarantined = 0
            cursor = 0
            while True:
                cursor, keys = POPOTO_REDIS_DB.scan(cursor=cursor, match="Room:*", count=500)
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else str(key)
                    if "::" in key_str:
                        continue  # companion key (inbox), not a record hash
                    if POPOTO_REDIS_DB.type(key) != b"hash":
                        continue
                    fields = POPOTO_REDIS_DB.hgetall(key)
                    decoded = {(k.decode() if isinstance(k, bytes) else k) for k in fields}
                    if "project_key" not in decoded or "addressee" not in decoded:
                        POPOTO_REDIS_DB.delete(key)
                        quarantined += 1
                        logger.warning(
                            "[room] quarantined identity-less Room hash %s "
                            "(missing KeyField data — would re-index as a phantom)",
                            key_str,
                        )
                if cursor == 0:
                    break

            rebuilt = cls.rebuild_indexes()
            return (quarantined, rebuilt if isinstance(rebuilt, int) else 0)
        finally:
            cls._repair_lock.release()
