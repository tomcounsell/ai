"""Synchronous per-tool budget backstop (Fix #6, issue #1821).

A pure ALLOW/DENY evaluator (the omnigent ``enforcement.py`` model — explicitly
NOT a background monitor) called inline from BOTH PreToolUse hook surfaces:
``agent/hooks/pre_tool_use.py`` (SDK/headless path) and
``.claude/hooks/pre_tool_use.py`` (interactive ``claude`` TUI / granite-PTY
path). Because the check runs at the point each tool call is dispatched, it
denies a runaway session inline even when every background health loop is frozen
— which is the whole point (the existing ``_agent_session_tool_timeout_loop`` is
a background monitor that a frozen worker loop stops running).

Separation of concerns:
- ``evaluate_tool_budget(session)`` is PURE and SYNCHRONOUS — a plain function
  that computes a verdict from the session's attributes alone (in-path,
  non-blocking, side-effect-free). The CALLER (hook) actuates a deny.
- ``record_budget_trip`` / ``record_resolution_error`` perform the fail-quiet
  side effects (counters, the hook-owned ``budget_tripped`` flag, and — only
  when ``TOOL_BUDGET_AUTO_PAUSE`` is set — the status→``paused_budget``
  transition + a Telegram ping). A surfacing error NEVER flips a deny to an
  allow nor crashes; the caller's inline block always proceeds independently.

Config: the env vars are read via raw ``os.environ.get()`` at module scope, NOT
through ``config/settings.py`` — matching the sibling precedent
(``WORKER_HEARTBEAT_INTERVAL`` and the #1815/#1820 threshold constants use the
same raw-``os.environ`` pattern). Tests override the module-level constants
directly (``monkeypatch.setattr``), which the functions read as globals at call
time.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _env_true(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in ("", "0", "false", "no")


# --- Provisional thresholds (env-overridable) --------------------------------
# All thresholds ship CONSERVATIVE-PROVISIONAL — well above any observed healthy
# session — and are meant to be tuned after observing real per-session
# tool-call / cost distributions on the live bridge machine.

# Max tool calls a single session may issue before the inline deny fires.
# Provisional, tune after observing real rates.
#
# Granite shared-counter caveat: on the granite path ``tool_call_count`` sums PM
# + Dev sub-agent tool calls (each sub-agent burns the same session counter), so
# the effective per-role ceiling is ~half this value and a trip can deny BOTH PM
# and Dev mid-build. That is bounded by this conservative default and the
# TOOL_BUDGET_ENABLED kill-switch — a MAX-tuning consideration (granite may want
# a higher MAX), NOT a reason to gate the deny off.
MAX_TOOL_CALLS_PER_SESSION = int(os.environ.get("MAX_TOOL_CALLS_PER_SESSION", "1000"))

# Per-session cost cap in USD. Provisional, tune after observing real rates.
#
# SDK/headless-path-only — currently a NO-OP on granite sessions: nothing under
# ``agent/granite_container/`` populates ``total_cost_usd`` (the interactive TUI
# transcript carries no cost line), so on granite ``total_cost_usd`` stays 0.0
# and this cap can never fire. ``total_cost_usd`` is written solely by
# ``agent/sdk_client.py`` (SDK ``ResultMessage.total_cost_usd`` + the headless
# ``claude -p stream-json`` ``result`` event). The cost dimension is kept
# (harmless: cost=0 → allow) but in production only the tool-call cap is the
# operative granite backstop.
SESSION_COST_CAP_USD = float(os.environ.get("SESSION_COST_CAP_USD", "50.0"))

# Master switch: enables the budget AND the inline DENY. DEFAULT ON — a deny
# verdict actuates the inline block/exit-2 by default, so the backstop actually
# backstops (Acceptance #2), even under a frozen health loop.
# ``TOOL_BUDGET_ENABLED=false`` is the instant kill-switch if the cap misfires.
TOOL_BUDGET_ENABLED = _env_true("TOOL_BUDGET_ENABLED", "true")

# Auto-pause switch: gates ONLY the DISRUPTIVE extras a deny additionally
# performs — the status→``paused_budget`` transition AND the Telegram ping.
# DEFAULT OFF. With it off, a deny still blocks the call inline + counts + logs +
# sets the ``budget_tripped`` flag, but the session ``status`` is LEFT UNTOUCHED
# — so nothing moves the session into a drip-eligible state and no runaway
# pending→denied→paused→pending loop can form.
TOOL_BUDGET_AUTO_PAUSE = os.environ.get("TOOL_BUDGET_AUTO_PAUSE", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Dedup / TTL windows for the fail-quiet surfacing side effects.
TRIP_DEDUP_TTL = 86400  # once-per-session gate for the counter/flag/surfacing
BUDGET_REACTION_DEDUP_TTL = 86400
BUDGET_REACTION_OUTBOX_TTL = 3600
# Telegram reaction emoji surfaced on the originating message when a session is
# auto-paused on a budget trip (only under TOOL_BUDGET_AUTO_PAUSE). Chosen from
# the Telegram-allowed reaction set.
BUDGET_REACTION_EMOJI = "🤯"


@dataclass
class BudgetVerdict:
    """Outcome of a per-tool budget evaluation.

    ``allow=True`` → the tool call proceeds. ``allow=False`` → the caller
    actuates a DENY (SDK ``{"decision":"block"}`` / CLI stderr + ``exit 2``);
    ``reason`` is the human-legible cause (dimension + value).
    """

    allow: bool
    reason: str | None = None


def evaluate_tool_budget(session) -> BudgetVerdict:
    """Return the per-session budget verdict — PURE and SYNCHRONOUS.

    Decides deny/allow only; the CALLER (hook) actuates the inline block on a
    deny (gated by ``TOOL_BUDGET_ENABLED``) and, only when
    ``TOOL_BUDGET_AUTO_PAUSE`` is set, the status→``paused_budget`` transition +
    Telegram ping.

    Fail-safe on missing data: ``None`` session, or ``None``/missing
    ``tool_call_count`` / ``total_cost_usd``, → ``allow`` (never a false deny on
    absent data). Also allows when ``TOOL_BUDGET_ENABLED`` is off.
    """
    if not TOOL_BUDGET_ENABLED or session is None:
        return BudgetVerdict(allow=True)
    calls = int(getattr(session, "tool_call_count", 0) or 0)
    cost = float(getattr(session, "total_cost_usd", 0.0) or 0.0)
    if calls >= MAX_TOOL_CALLS_PER_SESSION:
        return BudgetVerdict(
            False,
            f"per-session tool-call budget reached ({calls}/{MAX_TOOL_CALLS_PER_SESSION})",
        )
    # Cost branch is dead on granite (cost stays 0.0); live only on SDK/headless.
    if cost >= SESSION_COST_CAP_USD:
        return BudgetVerdict(
            False,
            f"per-session cost cap reached (${cost:.2f}/${SESSION_COST_CAP_USD:.2f})",
        )
    return BudgetVerdict(allow=True)


def _project_key_env() -> str:
    """Project-scoped Redis key prefix from env, falling back to ``valor``.

    Used when no session is resolvable (the resolution-error path) — mirrors
    ``reflections/agents/session_recovery_drip._get_project_key``.
    """
    v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
    return v or "valor"


def _project_key(session) -> str:
    pk = getattr(session, "project_key", None)
    if pk:
        return pk
    return _project_key_env()


def record_resolution_error(project_key: str, err: object, *, surface: str = "hook") -> None:
    """Loudly record that the backstop went BLIND on an infra/resolution error.

    A resolution failure must NOT brick tool calls (the caller still fails
    OPEN), but — unlike a genuine no-session — it means the budget cannot see
    sessions during exactly the partially-wedged Redis conditions it exists to
    guard. So we log LOUDLY at WARNING and increment
    ``{project_key}:tool-budget:resolution_errors`` (a rising counter is an
    operator signal, surfaced on the dashboard). Never raises.
    """
    logger.warning(
        "[tool-budget] backstop is BLIND this call (%s resolution error): %s",
        surface,
        err,
    )
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.incr(f"{project_key}:tool-budget:resolution_errors")
    except Exception as e:
        logger.warning("[tool-budget] failed to increment resolution_errors counter: %s", e)


def record_budget_trip(session, verdict: BudgetVerdict) -> None:
    """Fail-quiet deny surfacing — the caller's inline block proceeds regardless.

    On EVERY deny (default included), once per session (dedup ``SET NX``):
    increment ``{project_key}:tool-budget:tripped``, log a WARNING, and set the
    race-free hook-owned ``budget_tripped`` + ``budget_tripped_reason`` fields
    (a FIELD write, NEVER a ``status`` write — a hook-driven status write would
    race the granite ``bridge_adapter``'s partitioned ``update_fields`` saves).

    Only when ``TOOL_BUDGET_AUTO_PAUSE`` is set does the deny ALSO (b) transition
    status → ``paused_budget`` (via the status owner, ``transition_status`` — the
    CAS path, not a raw write) and (c) queue a user-visible Telegram reaction on
    the originating message.

    All side effects are best-effort: any error is caught and logged; it NEVER
    flips the deny to an allow nor crashes.
    """
    session_id = getattr(session, "session_id", None) or getattr(session, "agent_session_id", None)
    try:
        project_key = _project_key(session)
        from popoto.redis_db import POPOTO_REDIS_DB

        # Dedup the SIDE EFFECTS (not the block) to once per session.
        # #1873 item 3: an id-less session (no session_id AND no agent_session_id)
        # must NOT collapse into a shared ``...:tripped_applied:None`` slot — that
        # single key would silently dedup away every id-less trip after the first.
        if not session_id:
            # Bypass the NX dedup gate entirely: fall through to counter/log/flag
            # and surface on every id-less deny (never write a shared :None key).
            pass
        else:
            dedup_key = f"{project_key}:tool-budget:tripped_applied:{session_id}"
            first = POPOTO_REDIS_DB.set(dedup_key, "1", nx=True, ex=TRIP_DEDUP_TTL)
            if not first:
                return  # already surfaced this session; the block still fired in the caller

        try:
            POPOTO_REDIS_DB.incr(f"{project_key}:tool-budget:tripped")
        except Exception as e:
            logger.warning("[tool-budget] failed to increment tripped counter: %s", e)

        logger.warning(
            "[tool-budget] session %s tripped budget — DENY: %s",
            session_id,
            verdict.reason,
        )

        _set_budget_tripped_flag(session, verdict)

        if TOOL_BUDGET_AUTO_PAUSE:
            _auto_pause_and_notify(session, verdict)
    except Exception as e:
        logger.warning(
            "[tool-budget] deny-surfacing failed (non-fatal) for %s: %s",
            session_id,
            e,
        )


def _set_budget_tripped_flag(session, verdict: BudgetVerdict) -> None:
    """Set the race-free hook-owned ``budget_tripped`` fields (never ``status``).

    Uses a narrow ``save(update_fields=...)`` so no other field is clobbered.
    ``budget_tripped`` / ``budget_tripped_reason`` are fields NO other writer
    touches, so they are always race-free (unlike ``status``, which the granite
    adapter writes through its own partitioned saves).
    """
    try:
        session.budget_tripped = True
        session.budget_tripped_reason = f"per-session tool budget reached: {verdict.reason}"
        session.save(update_fields=["budget_tripped", "budget_tripped_reason", "updated_at"])
    except Exception as e:
        logger.warning(
            "[tool-budget] failed to set budget_tripped flag for %s: %s",
            getattr(session, "session_id", "?"),
            e,
        )


def _auto_pause_and_notify(session, verdict: BudgetVerdict) -> None:
    """Disruptive extras gated behind ``TOOL_BUDGET_AUTO_PAUSE`` — fail-quiet.

    (b) Transition status → ``paused_budget`` via the status owner
    (``transition_status`` — the CAS-protected path, not a raw write), and
    (c) queue a user-visible Telegram reaction. Each is independently best-effort
    so one failure cannot block the other, and neither can flip the caller's
    deny.
    """
    try:
        from models.session_lifecycle import transition_status

        transition_status(session, "paused_budget", reason=f"tool budget: {verdict.reason}")
    except Exception as e:
        logger.warning(
            "[tool-budget] paused_budget transition failed (non-fatal) for %s: %s",
            getattr(session, "session_id", "?"),
            e,
        )

    try:
        _queue_budget_reaction(session)
    except Exception as e:
        logger.warning(
            "[tool-budget] budget reaction queue failed (non-fatal) for %s: %s",
            getattr(session, "session_id", "?"),
            e,
        )


def _queue_budget_reaction(session) -> None:
    """Queue a user-visible reaction on the originating Telegram message.

    Mirrors ``monitoring/session_watchdog.py::_apply_stall_reaction`` — the same
    atomic ``SET NX EX`` dedup + reaction-queue write. Skips silently when the
    session has no ``chat_id`` / ``telegram_message_id`` / resolvable id, or when
    the dedup key already exists.
    """
    import json
    import time

    chat_id = getattr(session, "chat_id", None)
    msg_id = getattr(session, "telegram_message_id", None)
    session_id = getattr(session, "session_id", None) or getattr(session, "agent_session_id", None)
    if not (chat_id and msg_id and session_id):
        return

    from popoto.redis_db import POPOTO_REDIS_DB

    dedup_key = f"tool-budget:reaction_applied:{session_id}"
    slot_open = POPOTO_REDIS_DB.set(dedup_key, "1", nx=True, ex=BUDGET_REACTION_DEDUP_TTL)
    if not slot_open:
        return

    payload = {
        "type": "reaction",
        "chat_id": str(chat_id),
        "reply_to": int(msg_id),
        "emoji": BUDGET_REACTION_EMOJI,
        "session_id": session_id,
        "timestamp": time.time(),
    }
    queue_key = f"telegram:outbox:{session_id}"
    POPOTO_REDIS_DB.rpush(queue_key, json.dumps(payload))
    POPOTO_REDIS_DB.expire(queue_key, BUDGET_REACTION_OUTBOX_TTL)
    logger.warning("[tool-budget] budget reaction queued for %s", session_id)
