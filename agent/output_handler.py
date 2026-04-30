"""
Output handler protocol and implementations for agent session output routing.

Defines the OutputHandler protocol that all output destinations must implement,
plus built-in implementations for file logging and stderr logging. The bridge
registers its Telegram-specific handler; standalone workers use
TelegramRelayOutputHandler (with FileOutputHandler dual-write).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Default directory for worker output logs
WORKER_LOGS_DIR = Path(__file__).parent.parent / "logs" / "worker"


@runtime_checkable
class OutputHandler(Protocol):
    """Protocol for routing agent session output to a destination.

    Implementations must provide send() for text output and react() for
    emoji reactions. Both methods are async to support I/O-bound destinations
    (Telegram, email, etc.).
    """

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Send text output to the destination.

        Args:
            chat_id: Target chat/channel identifier.
            text: Message text to send.
            reply_to_msg_id: Original message ID to reply to.
            session: Optional session context object.
        """
        ...

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """Set a reaction emoji on a message.

        Args:
            chat_id: Target chat/channel identifier.
            msg_id: Message ID to react to.
            emoji: Emoji string to set, or None to clear.
        """
        ...


class FileOutputHandler:
    """Write agent output to log files in logs/worker/.

    Each session gets its own log file at logs/worker/{session_id}.log.
    Output includes timestamps for human readability.
    """

    def __init__(self, log_dir: Path | None = None):
        self.log_dir = log_dir or WORKER_LOGS_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Append text output to the session's log file."""
        if not text:
            return

        session_id = getattr(session, "session_id", None) or chat_id
        log_path = self.log_dir / f"{session_id}.log"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        entry = f"[{timestamp}] chat={chat_id} reply_to={reply_to_msg_id}\n{text}\n---\n"

        try:
            with open(log_path, "a") as f:
                f.write(entry)
            logger.info(f"Worker output written to {log_path.name} ({len(text)} chars)")
        except Exception as e:
            logger.error(f"Failed to write worker output to {log_path}: {e}")

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """Log the reaction (no-op for file output)."""
        session_id = chat_id  # Best effort
        log_path = self.log_dir / f"{session_id}.log"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        entry = f"[{timestamp}] REACTION chat={chat_id} msg={msg_id} emoji={emoji}\n"

        try:
            with open(log_path, "a") as f:
                f.write(entry)
        except Exception:
            pass  # Reactions are best-effort for file output


class TelegramRelayOutputHandler:
    """Route agent output to the Redis outbox for Telegram delivery.

    Writes JSON payloads to ``telegram:outbox:{session_id}`` using the same
    format as ``tools/send_telegram.py``.  The bridge relay
    (``bridge/telegram_relay.py``) polls these keys and delivers via Telethon.

    Every ``send()`` invocation routes through
    ``bridge.message_drafter.draft_message`` before the payload is written to
    Redis. This closes the worker-bypass gap where worker-executed PM sessions
    previously wrote raw text straight to the outbox, producing
    ``MessageTooLongError`` on content >4096 chars (see
    docs/plans/completed/message-drafter.md §Problem). Drafter errors fall
    through to raw-text delivery via the inner ``try/except`` block.

    An optional *file_handler* enables dual-write so output is also persisted
    to the local file log for debugging and audit. Redis errors are caught and
    logged -- they never propagate to the caller.
    """

    # TTL applied to each outbox key (seconds). Matches tools/send_telegram.py.
    OUTBOX_TTL = 3600

    def __init__(
        self,
        redis_url: str | None = None,
        file_handler: FileOutputHandler | None = None,
    ):
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._file_handler = file_handler
        self._redis = None  # Lazy connection

    def _get_redis(self):
        """Return a Redis connection, creating one lazily on first use."""
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _resolve_transport(session: Any) -> str:
        """Extract the transport from a session's extra_context.

        Returns "telegram" when the session is None, has no extra_context, or
        the transport key is missing. This preserves back-compat with sessions
        created before email transport existed.
        """
        if session is None:
            return "telegram"
        extra = getattr(session, "extra_context", None) or {}
        return extra.get("transport") or "telegram"

    async def _send_via_email_outbox(
        self,
        chat_id: str,
        text: str,
        session: Any,
    ) -> None:
        """Queue an email reply on ``email:outbox:{session_id}``.

        Builds a payload matching ``tools/send_message.py::_send_via_email``
        and the unified shape consumed by ``bridge/email_relay.py``. Subject
        is prefixed with ``"Re: "`` if the original subject does not already
        start with ``re:`` (case-insensitive); ``in_reply_to`` and
        ``references`` are sourced from ``extra_context.email_message_id``.

        Implements the user-set design rule (2026-04-30): when a session was
        spawned via the email bridge, the default Stop-drafter reply must
        route through the email outbox even when no project-specific email
        handler is registered. This handler is the worker's catch-all default;
        without this branch, email-spawned sessions silently misroute to
        telegram and the SMTP relay never sees them.
        """
        session_id = getattr(session, "session_id", None) or chat_id

        # Pull email metadata stamped on the session by bridge/email_bridge.py
        # (or the test skill's spawn.py). Missing fields fall back to safe
        # defaults so a malformed session still produces a valid envelope.
        extra = getattr(session, "extra_context", None) or {}
        original_subject = extra.get("email_subject") or ""
        in_reply_to = extra.get("email_message_id") or None

        # Subject prefixing: match bridge/email_bridge.py::_build_reply_mime
        # worker-reply semantics — always prepend "Re: " unless the subject
        # already starts with "re:" (case-insensitive). Empty subject becomes
        # "Re: (no subject)" so threading still works in the recipient's client.
        if original_subject:
            if original_subject.lower().startswith("re:"):
                subject = original_subject
            else:
                subject = f"Re: {original_subject}"
        else:
            subject = "Re: (no subject)"

        payload = {
            "session_id": session_id,
            "to": chat_id,
            "subject": subject,
            "body": text,
            "attachments": [],
            "in_reply_to": in_reply_to,
            "references": in_reply_to,
            "from_addr": os.environ.get("SMTP_USER", ""),
            "timestamp": time.time(),
        }

        queue_key = f"email:outbox:{session_id}"
        try:
            r = self._get_redis()
            r.rpush(queue_key, json.dumps(payload))
            r.expire(queue_key, self.OUTBOX_TTL)
            logger.info(
                "Queued email output to %s (%d chars, to=%s, in_reply_to=%s)",
                queue_key,
                len(text),
                chat_id,
                bool(in_reply_to),
            )
        except Exception as e:
            logger.error(f"Failed to write to Redis outbox {queue_key}: {e}")

        # Dual-write to file handler for audit/debugging (matches the telegram
        # path so worker logs preserve a record of every send attempt).
        if self._file_handler is not None:
            await self._file_handler.send(chat_id, text, 0, session)

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Write a message payload to the Redis outbox.

        Routes by ``session.extra_context.transport``:

        - ``telegram`` (default): payload format matches
          ``tools/send_telegram.py:145-151``::

              {"chat_id", "reply_to", "text", "session_id", "timestamp"}

          Written to ``telegram:outbox:{session_id}``.
        - ``email``: payload matches ``tools/send_message.py::_send_via_email``
          and ``bridge/email_relay.py``'s expected schema. Written to
          ``email:outbox:{session_id}``. This implements the "default reply
          follows spawning bridge" rule (user decision 2026-04-30): a session
          spawned via the email bridge replies via email by default, even if
          this handler is registered as the project's catch-all.

        Args:
            chat_id: Target chat identifier (Telegram chat_id or recipient
                email address depending on transport).
            text: Message text to send.
            reply_to_msg_id: Original message ID to reply to (may be None).
                Ignored for email transport.
            session: Optional AgentSession providing ``session_id`` and
                ``extra_context`` (transport, email_message_id, etc.).
        """
        if not text:
            return

        # Transport-aware routing: redirect email-spawned sessions to the
        # email outbox. Default (no transport key, or any non-email value) is
        # telegram, preserving back-compat with older sessions.
        transport = self._resolve_transport(session)
        if transport == "email":
            await self._send_via_email_outbox(chat_id, text, session)
            return

        session_id = getattr(session, "session_id", None) or chat_id
        reply_to = int(reply_to_msg_id) if reply_to_msg_id else None

        # Run the drafter in-process BEFORE writing to the outbox. This is the
        # critical fix from docs/plans/message-drafter.md §Part C — it closes
        # the worker-bypass gap where the worker's send_cb path wrote raw text
        # straight to Redis and bypassed all length/format compliance.
        delivery_text = text
        file_paths: list[str] | None = None
        steering_deferred = False
        try:
            from bridge.message_drafter import draft_message

            draft = await draft_message(
                text,
                session=session,
                medium="telegram",
            )
            # If the drafter produced a drafted result, use its text. When
            # was_drafted is False (short output or empty), keep the original
            # raw text (drafter returns it verbatim in that case).
            if draft.text:
                delivery_text = draft.text
            if draft.full_output_file is not None:
                file_paths = [str(draft.full_output_file)]

            # ── Self-draft fallback via session steering ──
            # When all drafter backends fail (needs_self_draft=True), inject
            # a steering message asking the agent to self-draft. This
            # mirrors the pre-consolidation behavior from the deleted
            # bridge/response.py::send_response_with_files. Silent failure:
            # any error here MUST NOT block delivery.
            if getattr(draft, "needs_self_draft", False):
                steering_deferred = self._inject_self_draft_steering(session)
                if not steering_deferred:
                    # Steering unavailable or failed — apply narration gate
                    # on the original text as a last resort. Substitutes the
                    # NARRATION_FALLBACK_MESSAGE when the raw text is pure
                    # process narration with no substantive content.
                    delivery_text = self._apply_narration_fallback(text)

            # ── Persist routing fields to session ──
            # When the drafter succeeds, write context_summary and
            # expectations back to the AgentSession. bridge/session_router.py
            # and bridge/telegram_bridge.py still read session.expectations
            # from the outbound path. Silent failure.
            if session is not None and getattr(draft, "was_drafted", False):
                self._persist_routing_fields(session, draft)
        except Exception as e:
            # Drafter failure MUST NOT block delivery. Fall back to raw text;
            # the relay length guard (bridge/telegram_relay.py) catches any
            # oversize payloads as a last line of defense.
            logger.warning(
                "Drafter failed in TelegramRelayOutputHandler.send (%s); falling back to raw text",
                e,
            )

        # If steering was deferred, the agent will self-draft on the next turn.
        # Skip the outbox write but still dual-write to file for audit.
        if steering_deferred:
            logger.info(
                "Delivery deferred to agent self-draft (steering injected) for session %s",
                session_id,
            )
            if self._file_handler is not None:
                await self._file_handler.send(chat_id, text, reply_to_msg_id, session)
            return

        # ── Read-the-Room pre-send pass (issue #1193) ──
        # Lightweight Haiku call inspects the chat snapshot + the drafted
        # message and returns one of {send, trim, suppress}. RTR is a
        # *guard*, not a blocker: every error path returns send and the
        # delivery proceeds with the original delivery_text. RTR is gated
        # by the READ_THE_ROOM_ENABLED env var (default off) and short-
        # circuits for SDLC sessions, short outputs, empty drafts, missing
        # chat_ids, and empty snapshots.
        try:
            from bridge.read_the_room import (
                RTR_SUPPRESS_EMOJI,
                TRIM_TOO_SHORT_THRESHOLD,
                read_the_room,
            )

            verdict = await read_the_room(delivery_text, chat_id, session)

            if verdict.action == "trim" and verdict.revised_text:
                if len(verdict.revised_text) < TRIM_TOO_SHORT_THRESHOLD:
                    # F4: too-short trim coerces to suppress -- a single emoji
                    # landing in a personal exchange is the exact failure mode
                    # this feature exists to prevent.
                    self._rtr_emit_event(
                        session,
                        "rtr.suppressed",
                        chat_id=chat_id,
                        draft_text=delivery_text,
                        revised_text=verdict.revised_text,
                        reason="trim_too_short",
                    )
                    if reply_to_msg_id is not None:
                        self._rtr_queue_reaction(
                            chat_id, reply_to_msg_id, RTR_SUPPRESS_EMOJI, session_id
                        )
                        if self._file_handler is not None:
                            await self._file_handler.send(chat_id, text, reply_to_msg_id, session)
                        return
                    # No anchor: fall through to send original.
                    # F4: silent suppression breaks the I-heard-you contract --
                    # fall-through preserves the audit signal
                    self._rtr_emit_event(
                        session,
                        "rtr.suppress_fallthrough",
                        chat_id=chat_id,
                        draft_text=delivery_text,
                        reason="no_reply_anchor",
                    )
                else:
                    # Long-form trim: substitute the revised text.
                    self._rtr_emit_event(
                        session,
                        "rtr.trimmed",
                        chat_id=chat_id,
                        draft_text=delivery_text,
                        revised_text=verdict.revised_text,
                        reason=verdict.reason or "trim",
                    )
                    delivery_text = verdict.revised_text
            elif verdict.action == "suppress":
                self._rtr_emit_event(
                    session,
                    "rtr.suppressed",
                    chat_id=chat_id,
                    draft_text=delivery_text,
                    reason=verdict.reason or "suppress",
                )
                if reply_to_msg_id is not None:
                    self._rtr_queue_reaction(
                        chat_id, reply_to_msg_id, RTR_SUPPRESS_EMOJI, session_id
                    )
                    if self._file_handler is not None:
                        await self._file_handler.send(chat_id, text, reply_to_msg_id, session)
                    return
                # No anchor for the 👀 reaction. F4: silent suppression breaks
                # the I-heard-you contract -- fall-through preserves the audit
                # signal by sending the original draft text and logging.
                self._rtr_emit_event(
                    session,
                    "rtr.suppress_fallthrough",
                    chat_id=chat_id,
                    draft_text=delivery_text,
                    reason="no_reply_anchor",
                )
            # else: send (or trim with no revised_text) -- fall through to
            # the existing outbox write path with delivery_text unchanged.
        except Exception as rtr_err:
            # Fail-open: RTR errors NEVER block delivery. read_the_room()
            # already catches its own errors and returns send; this outer
            # guard catches anything that escapes (import error, helper
            # crash, etc.).
            logger.warning(
                "RTR call failed in TelegramRelayOutputHandler.send (%s); "
                "falling back to original delivery_text",
                rtr_err,
            )

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "reply_to": reply_to,
            "text": delivery_text,
            "session_id": session_id,
            "timestamp": time.time(),
        }
        if file_paths:
            payload["file_paths"] = file_paths

        queue_key = f"telegram:outbox:{session_id}"
        try:
            r = self._get_redis()
            r.rpush(queue_key, json.dumps(payload))
            r.expire(queue_key, self.OUTBOX_TTL)
            logger.info(
                "Queued output to %s (%d chars, files=%d)",
                queue_key,
                len(delivery_text),
                len(file_paths or []),
            )
        except Exception as e:
            logger.error(f"Failed to write to Redis outbox {queue_key}: {e}")

        # Dual-write to file handler for audit/debugging
        if self._file_handler is not None:
            await self._file_handler.send(chat_id, text, reply_to_msg_id, session)

    def _inject_self_draft_steering(self, session: Any) -> bool:
        """Push a self-draft instruction to the session's steering queue.

        Called when ``draft.needs_self_draft`` is True (all LLM drafter backends
        failed). The agent will notice the steering message at its next turn
        boundary and re-draft its own output.

        Includes loop prevention via ``peek_steering_sender``: if a prior
        self-draft steering message is already pending, returns False so the
        caller falls through to the narration gate rather than looping.

        Returns:
            True if steering was successfully pushed (delivery should be
            deferred), False if steering was skipped or failed (caller should
            apply narration fallback).
        """
        session_id = getattr(session, "session_id", None) if session else None
        if not session_id:
            return False

        # Loop prevention: don't push a second self-draft steering message if
        # one is already pending (the agent's self-draft also failed).
        try:
            from agent.steering import peek_steering_sender

            if peek_steering_sender(session_id) == "drafter-fallback":
                logger.warning(
                    "Self-summary steering already pending for session %s; "
                    "falling through to narration gate",
                    session_id,
                )
                return False
        except Exception:
            # peek failed, continue with steering attempt
            pass

        try:
            from agent.steering import push_steering_message
            from bridge.message_drafter import SELF_DRAFT_INSTRUCTION

            push_steering_message(
                session_id,
                SELF_DRAFT_INSTRUCTION,
                sender="drafter-fallback",
            )
            logger.info(
                "Injected self-summary steering for session %s (drafter failed)",
                session_id,
            )
            return True
        except Exception as steer_err:
            logger.warning(
                "Steering push failed (non-fatal) for session %s: %s",
                session_id,
                steer_err,
            )
            return False

    def _apply_narration_fallback(self, text: str) -> str:
        """Substitute NARRATION_FALLBACK_MESSAGE when text is pure narration.

        Invoked when the drafter fails AND self-draft steering is unavailable
        or already-pending. Mirrors the narration gate in the deleted
        ``bridge/response.py::send_response_with_files``.

        Returns the NARRATION_FALLBACK_MESSAGE if ``is_narration_only`` judges
        the first 500 chars of ``text`` to be pure process narration.
        Otherwise returns the original text unchanged.
        """
        try:
            from bridge.message_quality import (
                NARRATION_FALLBACK_MESSAGE,
                is_narration_only,
            )

            if is_narration_only(text[:500]):
                logger.info("Narration gate triggered on drafter fallback path")
                return NARRATION_FALLBACK_MESSAGE
        except Exception as narr_err:
            logger.warning(
                "Narration gate check failed (non-fatal): %s",
                narr_err,
            )
        return text

    def _persist_routing_fields(self, session: Any, draft: Any) -> None:
        """Write drafter-derived routing fields back to the AgentSession.

        ``draft.context_summary`` and ``draft.expectations`` are consumed by
        ``bridge/session_router.py`` and ``bridge/telegram_bridge.py`` for
        conversation routing. Silent failure: persistence errors MUST NOT
        block delivery.
        """
        try:
            context_summary = getattr(draft, "context_summary", None)
            expectations = getattr(draft, "expectations", None)

            if context_summary:
                session.context_summary = context_summary
            if expectations is not None:
                session.expectations = expectations

            if context_summary or expectations is not None:
                session.save()
                logger.debug(
                    "Persisted routing fields to session %s (context_summary=%s, expectations=%s)",
                    getattr(session, "session_id", "<unknown>"),
                    bool(context_summary),
                    bool(expectations),
                )
        except Exception as persist_err:
            # Non-fatal: routing field persistence should never block delivery
            logger.warning(
                "Failed to persist routing fields (non-fatal): %s",
                persist_err,
            )

    # === Read-the-Room helpers (issue #1193) ===

    def _rtr_emit_event(
        self,
        session: Any,
        event_type: str,
        *,
        chat_id: str,
        draft_text: str,
        revised_text: str | None = None,
        reason: str = "",
    ) -> None:
        """Append an ``rtr.*`` entry to ``session.session_events``.

        Best-effort -- exceptions are swallowed. The codebase's existing
        session_events append posture is read-modify-write with no lock;
        we match it here. Race 3 (concurrent appends) is documented and
        accepted -- the surrounding event log is best-effort.
        """
        if session is None:
            return
        try:
            event: dict[str, Any] = {
                "type": event_type,
                "ts": time.time(),
                "chat_id": str(chat_id),
                "reason": reason,
                "draft_preview": draft_text[:200],
            }
            if revised_text is not None:
                event["revised_preview"] = revised_text[:200]
            events = list(getattr(session, "session_events", None) or [])
            events.append(event)
            session.session_events = events
            if hasattr(session, "save"):
                session.save()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("RTR event append failed (non-fatal): %s", e)

    def _rtr_queue_reaction(
        self,
        chat_id: str,
        reply_to_msg_id: int,
        emoji: str,
        session_id: str,
    ) -> None:
        """Queue a 👀 reaction directly to ``telegram:outbox:{session_id}``.

        Built via :meth:`_build_reaction_payload` so the schema matches
        :meth:`react` byte-for-byte. We do NOT call ``self.react()`` here:
        ``react()`` derives ``session_id = chat_id`` (line 411), which would
        orphan the reaction in a different queue when ``session.session_id
        != chat_id`` (the normal case). See Implementation Note F7.
        """
        payload = self._build_reaction_payload(chat_id, reply_to_msg_id, emoji, session_id)
        queue_key = f"telegram:outbox:{session_id}"
        try:
            r = self._get_redis()
            r.rpush(queue_key, json.dumps(payload))
            r.expire(queue_key, self.OUTBOX_TTL)
            logger.info("Queued RTR suppress reaction to %s (emoji=%s)", queue_key, emoji)
        except Exception as e:
            logger.error("Failed to write RTR reaction to Redis outbox %s: %s", queue_key, e)

    @staticmethod
    def _build_reaction_payload(
        chat_id: str,
        reply_to_msg_id: int | None,
        emoji: str | None,
        session_id: str,
        *,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Build a reaction payload dict for the Redis outbox.

        Single source of truth for the reaction payload schema. Used by both
        ``react()`` and the RTR suppress branch in ``send()`` so the two
        outbox writers can never drift apart (Implementation Note AD1).

        Args:
            chat_id: Target Telegram chat identifier.
            reply_to_msg_id: Message ID to react to. May be ``None``.
            emoji: Emoji string to set, or ``None`` to clear.
            session_id: Outbox queue key suffix; messages and reactions for
                the same session must use the same session_id so the relay
                serves them from one queue.
            timestamp: Override timestamp (for tests). Defaults to
                ``time.time()`` when None.
        """
        return {
            "type": "reaction",
            "chat_id": chat_id,
            "reply_to": int(reply_to_msg_id) if reply_to_msg_id else None,
            "emoji": str(emoji) if emoji is not None else None,
            "session_id": session_id,
            "timestamp": timestamp if timestamp is not None else time.time(),
        }

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """Write a reaction payload to the Redis outbox.

        Args:
            chat_id: Target Telegram chat identifier.
            msg_id: Message ID to react to.
            emoji: Emoji string to set, or None to clear.
        """
        # Derive a session_id -- best effort, use chat_id as fallback.
        # NOTE: when called with a session context, callers should prefer
        # writing the reaction directly to ``telegram:outbox:{session.session_id}``
        # via _build_reaction_payload (see RTR suppress branch in send()).
        session_id = chat_id

        payload = self._build_reaction_payload(chat_id, msg_id, emoji, session_id)

        queue_key = f"telegram:outbox:{session_id}"
        try:
            r = self._get_redis()
            r.rpush(queue_key, json.dumps(payload))
            r.expire(queue_key, self.OUTBOX_TTL)
            logger.info(f"Queued reaction to {queue_key} (emoji={emoji})")
        except Exception as e:
            logger.error(f"Failed to write reaction to Redis outbox {queue_key}: {e}")

        # Dual-write to file handler
        if self._file_handler is not None:
            await self._file_handler.react(chat_id, msg_id, emoji)
