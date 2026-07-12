"""Orchestration entry the bridge calls: triage -> gate -> action -> reply/audit.

``handle_customer_email`` runs the full email-CS pipeline inline in the bridge
(not via the worker/AgentSession machinery — latency, single-machine ownership,
and observability all favor inline; see the plan's OQ2). It returns a
``HandlerOutcome`` so ``_process_inbound_email`` can decide whether to still
spawn the fallback AgentSession.

Disposition contract:
- ``auto``  (Phase >= 2 only): run Tier 2, execute the whitelisted read-only
  verb, render a reply to ``email:outbox``, write an audit note.
  ``short_circuit=True`` (no fallback AgentSession).
- ``draft``: write a cuttlefish ``customer email draft`` + ping the Cuttlefish
  Telegram chat + audit note. No customer-facing send.
  ``short_circuit=True``.
- ``escalate``: ping the Cuttlefish Telegram chat + audit note. Fall through to
  the existing AgentSession spawn (``short_circuit=False``) so a human path
  always exists.

Shadow mode (Phase 1, default): classify + gate + write an audit note recording
the verdict, but SEND NOTHING to the customer and DO NOT short-circuit — the
existing AgentSession spawn remains the operator path while the classifier is
calibrated against real inbound.

Fail-safe: every exception inside this function resolves to escalate (fall
through), logged at WARNING/ERROR. A failure never silently auto-handles, and a
failed Telegram ping never swallows the audit note.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from .agents import run_action_agent
from .cuttlefish import CuttlefishCommandError, run_manage_command
from .gate import decide
from .schema import Category, Disposition
from .triage import triage_local

logger = logging.getLogger(__name__)


@dataclass
class HandlerOutcome:
    """Result of the email-CS pipeline for one inbound email.

    ``short_circuit`` tells the bridge whether to SKIP the fallback AgentSession
    spawn. True only for fully-handled auto/draft lanes in non-shadow phases.
    """

    disposition: Disposition
    category: Category
    short_circuit: bool
    reason: str = ""
    audit_written: bool = False
    customer_replied: bool = False


def _email_cs_config(project: dict) -> dict:
    """Pull the email-CS sub-config from the project's email block (or {})."""
    return (project.get("email") or {}).get("customer_service") or {}


def _is_enabled(project: dict) -> bool:
    """True if this project has an email-CS config wired (else the layer is inert).

    The layer only runs for projects that declare both a customer_resolver and an
    email.customer_service block. Absent either, ``handle_customer_email`` is a
    no-op and the bridge keeps its existing behavior.
    """
    return bool(project.get("customer_resolver")) and bool(_email_cs_config(project))


async def handle_customer_email(
    parsed: dict,
    project: dict,
    customer_id: str | None,
    *,
    session_id: str,
    shadow_mode: bool | None = None,
) -> HandlerOutcome | None:
    """Run the email-CS triage pipeline for a resolved customer email.

    Args:
        parsed: The parsed inbound email dict (subject, body, from_addr, ...).
        project: The project config dict from projects.json.
        customer_id: The resolved customer id (or None — escalates).
        session_id: The coalesced session id (for outbox keys + audit note).
        shadow_mode: Override the project's shadow_mode flag (mainly for tests).
            When None, read from the project email-CS config (default True).

    Returns:
        A ``HandlerOutcome``, or ``None`` if the layer is not configured for this
        project (inert — the bridge proceeds with its existing flow).
    """
    if not _is_enabled(project):
        return None

    cfg = _email_cs_config(project)
    if shadow_mode is None:
        shadow_mode = bool(cfg.get("shadow_mode", True))
    allow_mutations = bool(cfg.get("auto_mutations", False))
    working_directory = project.get("working_directory") or "~/src/cuttlefish"

    subject = parsed.get("subject") or ""
    body = parsed.get("body") or ""
    email = {"subject": subject, "body": body}

    # --- Tier 1 triage (fail-safe -> escalate) ---
    triage = await triage_local(subject, body, customer_id)

    # --- Escalation gate ---
    disposition = decide(triage)

    # --- Shadow mode: classify + audit only, send nothing, do not short-circuit ---
    if shadow_mode:
        await _write_audit_note(
            working_directory,
            customer_id,
            session_id,
            f"[shadow] verdict={disposition.value} category={triage.category.value} "
            f"conf={triage.confidence:.2f} signal={triage.escalation_signal or '-'} "
            f"reason={triage.reason}",
            cfg,
        )
        logger.info(
            f"[email_cs.handler] shadow verdict {disposition.value}/{triage.category.value} "
            f"session={session_id} (sending nothing, falling through to AgentSession)"
        )
        return HandlerOutcome(
            disposition=disposition,
            category=triage.category,
            short_circuit=False,
            reason=f"shadow:{triage.reason}",
            audit_written=True,
        )

    # --- Live mode ---
    if disposition == Disposition.ESCALATE:
        return await _handle_escalate(
            project, cfg, working_directory, customer_id, session_id, triage
        )

    # disposition == AUTO at the gate -> run Tier 2 to pick a concrete tool.
    try:
        action = await run_action_agent(
            triage.category, triage, email, allow_mutations=allow_mutations
        )
    except Exception as e:
        logger.warning(f"[email_cs.handler] Tier 2 raised, escalating: {e}")
        return await _handle_escalate(
            project,
            cfg,
            working_directory,
            customer_id,
            session_id,
            triage,
            extra_reason=f"tier2 exception: {e}",
        )

    if action.disposition == Disposition.ESCALATE:
        return await _handle_escalate(
            project,
            cfg,
            working_directory,
            customer_id,
            session_id,
            triage,
            extra_reason=action.reason,
        )
    if action.disposition == Disposition.DRAFT:
        return await _handle_draft(
            project, cfg, working_directory, customer_id, session_id, triage, action.reason
        )

    # AUTO: execute the whitelisted verb, audit, then reply.
    return await _handle_auto(
        project, cfg, working_directory, customer_id, session_id, parsed, triage, action
    )


async def _handle_auto(
    project, cfg, working_directory, customer_id, session_id, parsed, triage, action
) -> HandlerOutcome:
    """Execute the chosen read-only verb, write the audit note, render the reply.

    Order of operations (race 2 mitigation): write the audit note IMMEDIATELY
    after the command returns (before the reply) so a crash leaves a durable
    record. If rendering/queuing the reply fails after a successful command, we
    escalate-with-context rather than going silent.
    """
    try:
        result = await run_manage_command(
            action.verb_argv,
            customer_id,
            working_directory,
            extra_args=_args_to_argv(action.tool_args),
        )
    except CuttlefishCommandError as e:
        logger.warning(f"[email_cs.handler] manage.py failed, escalating: {e}")
        return await _handle_escalate(
            project,
            cfg,
            working_directory,
            customer_id,
            session_id,
            triage,
            extra_reason=f"manage.py failure: {e}",
        )

    # Audit FIRST (durable record before the customer-facing side effect).
    audit_ok = await _write_audit_note(
        working_directory,
        customer_id,
        session_id,
        f"[auto] {action.tool_name} ok; category={triage.category.value} "
        f"conf={triage.confidence:.2f}",
        cfg,
    )

    # Render + queue the reply.
    try:
        reply_body = _render_reply(triage.category, action.tool_name, result)
        await _queue_email_reply(parsed, session_id, reply_body)
        replied = True
    except Exception as e:
        # Mutation/lookup happened, reply did not — escalate WITH context so a
        # human is pinged (never silence).
        logger.error(f"[email_cs.handler] reply render/queue failed after command: {e}")
        await _ping_human(
            project,
            cfg,
            session_id,
            f"AUTO command {action.tool_name} for {customer_id} succeeded but the reply "
            f"failed to send: {e}. Manual follow-up needed.",
        )
        return HandlerOutcome(
            disposition=Disposition.ESCALATE,
            category=triage.category,
            short_circuit=True,
            reason=f"reply failed after command: {e}",
            audit_written=audit_ok,
        )

    return HandlerOutcome(
        disposition=Disposition.AUTO,
        category=triage.category,
        short_circuit=True,
        reason=action.reason,
        audit_written=audit_ok,
        customer_replied=replied,
    )


async def _handle_draft(
    project, cfg, working_directory, customer_id, session_id, triage, reason
) -> HandlerOutcome:
    """Queue a cuttlefish human-review draft + ping + audit. No customer send."""
    draft_ok = True
    try:
        await run_manage_command(
            ["customer", "email", "draft"],
            customer_id,
            working_directory,
            extra_args=["--session-id", session_id],
        )
    except CuttlefishCommandError as e:
        # Drafting failed — degrade to escalate (ping the human directly).
        logger.warning(f"[email_cs.handler] draft command failed, escalating: {e}")
        draft_ok = False
        reason = f"{reason}; draft failed: {e}"

    await _ping_human(
        project,
        cfg,
        session_id,
        f"DRAFT queued for {customer_id} (category={triage.category.value}): {reason}",
    )
    audit_ok = await _write_audit_note(
        working_directory,
        customer_id,
        session_id,
        f"[draft] category={triage.category.value} reason={reason}",
        cfg,
    )
    return HandlerOutcome(
        disposition=Disposition.DRAFT,
        category=triage.category,
        short_circuit=draft_ok,
        reason=reason,
        audit_written=audit_ok,
    )


async def _handle_escalate(
    project, cfg, working_directory, customer_id, session_id, triage, *, extra_reason=""
) -> HandlerOutcome:
    """Ping the Cuttlefish chat + audit note; fall through to AgentSession spawn."""
    reason = triage.reason if not extra_reason else f"{triage.reason}; {extra_reason}"
    await _ping_human(
        project,
        cfg,
        session_id,
        f"ESCALATE {customer_id} (category={triage.category.value}, "
        f"signal={triage.escalation_signal or '-'}): {reason}",
    )
    # Audit note must be written even if the ping failed.
    audit_ok = await _write_audit_note(
        working_directory,
        customer_id,
        session_id,
        f"[escalate] category={triage.category.value} "
        f"signal={triage.escalation_signal or '-'} reason={reason}",
        cfg,
    )
    return HandlerOutcome(
        disposition=Disposition.ESCALATE,
        category=triage.category,
        short_circuit=False,  # human path: keep the existing AgentSession spawn
        reason=reason,
        audit_written=audit_ok,
    )


def _args_to_argv(tool_args: dict) -> list[str]:
    """Flatten an action agent's tool args dict to ``--key value`` argv tokens.

    Reserved keys (``email``, ``json``) are stripped before flattening so that
    an agent-supplied ``email`` argument can never override the trusted
    ``customer_id`` that ``run_manage_command`` always injects, and ``json``
    cannot conflict with the ``--json`` flag that is always appended last.
    """
    # Keys the caller injects directly — agent must not override them.
    _reserved = {"email", "json"}
    argv: list[str] = []
    for k, v in (tool_args or {}).items():
        if str(k).lower() in _reserved:
            continue
        if v is None:
            continue
        argv.extend([f"--{str(k).replace('_', '-')}", str(v)])
    return argv


def _render_reply(category: Category, tool_name: str, result: dict) -> str:
    """Render a customer-facing reply body from a manage.py --json result.

    Minimal, safe rendering: a short acknowledgment plus the structured result
    summary. Phase 2 (read-only) only renders lookups (status/checkout-url), so
    a terse, accurate echo of the result is appropriate.
    """
    summary = result.get("message") or result.get("summary")
    if not summary:
        # Fall back to a compact JSON echo of safe top-level fields.
        safe = {k: v for k, v in result.items() if isinstance(v, str | int | float | bool)}
        summary = json.dumps(safe) if safe else "Your request has been processed."
    return f"Hi,\n\n{summary}\n\nBest,\nThe Cuttlefish team"


async def _queue_email_reply(parsed: dict, session_id: str, body: str) -> None:
    """Queue a customer-facing reply on ``email:outbox:{session_id}``.

    Builds the same envelope shape consumed by ``bridge/email_relay.py``
    (mirrors ``EmailOutputHandler._send_via_email_outbox``).
    """
    from bridge.email_bridge import _get_redis

    from_addr = parsed.get("from_addr") or ""
    original_subject = parsed.get("subject") or ""
    if original_subject.lower().startswith("re:"):
        subject = original_subject
    elif original_subject:
        subject = f"Re: {original_subject}"
    else:
        subject = "Re: (no subject)"

    in_reply_to = parsed.get("message_id") or None
    payload = {
        "session_id": session_id,
        "to": [from_addr],
        "subject": subject,
        "body": body,
        "attachments": [],
        "in_reply_to": in_reply_to,
        "references": in_reply_to,
        "timestamp": time.time(),
    }
    r = _get_redis()
    key = f"email:outbox:{session_id}"
    r.rpush(key, json.dumps(payload))
    r.expire(key, 86400)
    logger.info(f"[email_cs.handler] queued auto-reply to {key} ({len(body)} chars)")


async def _ping_human(project: dict, cfg: dict, session_id: str, text: str) -> bool:
    """Best-effort Telegram ping to the Cuttlefish chat. Never raises.

    Returns True if the ping was queued. A failed ping logs at ERROR but does
    NOT prevent the audit note from being written (the caller still audits).
    """
    chat_id = cfg.get("escalation_chat_id")
    if not chat_id:
        logger.warning(
            "[email_cs.handler] no escalation_chat_id configured; skipping Telegram ping "
            f"(session={session_id})"
        )
        return False
    try:
        from bridge.email_bridge import _get_redis

        payload = {
            "chat_id": chat_id,
            "text": f"[email-cs] {text}",
            "session_id": session_id,
            "timestamp": time.time(),
        }
        r = _get_redis()
        key = f"telegram:outbox:{session_id}"
        r.rpush(key, json.dumps(payload))
        r.expire(key, 86400)
        return True
    except Exception as e:
        logger.error(f"[email_cs.handler] Telegram ping failed (non-fatal): {e}")
        return False


async def _write_audit_note(
    working_directory: str, customer_id: str | None, session_id: str, body: str, cfg: dict
) -> bool:
    """Write a cuttlefish ``customer note`` audit record. Best-effort, never raises.

    Returns True if the note was written. Failures log at WARNING but never
    crash the pipeline — the audit trail is durable-best-effort.
    """
    if not customer_id:
        return False
    category = cfg.get("note_category", "general")
    try:
        await run_manage_command(
            ["customer", "note"],
            customer_id,
            working_directory,
            extra_args=["--body", body, "--category", category, "--session-id", session_id],
        )
        return True
    except Exception as e:
        logger.warning(f"[email_cs.handler] audit note write failed (non-fatal): {e}")
        return False
