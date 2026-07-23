"""Shared in-flight ``AgentSession`` resolver for the hook surface (issue #2205).

Two identifiers can be present in a harness subprocess's environment:

- ``VALOR_SESSION_ID`` -- the true ``AgentSession.session_id`` (e.g.
  ``tg_valor_...``, ``sdlc-local-...``). Injected by
  ``agent/session_executor.py`` alongside ``AGENT_SESSION_ID`` (issue #2206).
- ``AGENT_SESSION_ID`` -- the per-run Popoto AutoKey hex (the ``id`` field,
  e.g. ``agt_...``). Always present; historically the only identifier hooks
  read.

For bridge PM sessions ``agent_session_id != session_id``, so a lookup that
reads ``AGENT_SESSION_ID`` and filters on ``session_id`` silently misses.
``resolve_inflight_session()`` fixes this by preferring ``VALOR_SESSION_ID``
(a direct ``session_id`` filter) and falling through to ``AGENT_SESSION_ID``
(a primary-key ``get_by_id`` lookup) when the VALOR lookup is absent or comes
up empty.

Exception-propagation posture: this module does **not** swallow
Popoto/Redis errors. Callers that need a fail-open or fail-silent contract
(the SDK budget backstop, the liveness writers) apply their own try/except
around ``resolve_inflight_session()``.
"""

from __future__ import annotations

import os

from models.agent_session import AgentSession


def inflight_cooldown_key() -> str | None:
    """Return the stable per-session cooldown bucket base for this process.

    Pure env read -- no Popoto/Redis touch. Prefers ``VALOR_SESSION_ID``,
    falls back to ``AGENT_SESSION_ID``, else ``None``. Within one running
    harness subprocess the same env var is always present, so the returned
    base is stable for the lifetime of the session regardless of which
    identifier ``resolve_inflight_session()`` ultimately resolves via.
    Callers append their own per-metric suffix (e.g. ``:turn``,
    ``:thinking``).
    """
    return os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")


def resolve_inflight_session() -> AgentSession | None:
    """Resolve the in-flight ``AgentSession`` for the current hook invocation.

    Resolution order:
        1. ``VALOR_SESSION_ID`` (the true ``session_id``) -- looked up via
           ``AgentSession.query.filter(session_id=...)``. If it matches,
           return the first match.
        2. VALOR miss-fallthrough: if ``VALOR_SESSION_ID`` was set but
           produced zero rows, do NOT return ``None`` yet -- a stale or
           mismatched ``VALOR_SESSION_ID`` must not shadow a resolvable
           ``AGENT_SESSION_ID``. Fall through to step 3.
        3. ``AGENT_SESSION_ID`` (the AutoKey hex) -- looked up via
           ``AgentSession.get_by_id_strict(...)`` (a primary-key lookup, the
           correct match for that identifier shape). May return ``None``.
        4. If neither env var is set, return ``None``.

    Returns ``None`` for a genuine no-session (env unset / no matching
    record anywhere). Does NOT swallow Popoto/Redis exceptions -- uses the
    ``_strict`` lookup variants so infra errors propagate to the caller
    (``get_by_id`` swallows its own exceptions; ``get_by_id_strict`` does
    not -- see ``models/agent_session.py``).
    """
    valor_session_id = os.environ.get("VALOR_SESSION_ID")
    if valor_session_id:
        matches = list(AgentSession.query.filter(session_id=valor_session_id))
        if matches:
            return matches[0]
        # VALOR miss-fallthrough: fall through to AGENT_SESSION_ID below.

    agent_session_id = os.environ.get("AGENT_SESSION_ID")
    if agent_session_id:
        return AgentSession.get_by_id_strict(agent_session_id)

    return None
