"""Compatibility surface for self-healing reflections relocated to reflections/agents/.

The five self-healing reflection callables that used to live here now each have
their own self-contained module under ``reflections/agents/`` (one file per
reflection — see issue #1028). The reflections registry (config/reflections.yaml,
vault) still references the historical dotted paths below, so each reflection is
re-exported here under its original name and the scheduler's importlib resolution
keeps working with no registry edit:

- ``agent.sustainability.circuit_health_gate``    → reflections.agents.circuit_health_gate.run
- ``agent.sustainability.session_recovery_drip``  → reflections.agents.session_recovery_drip.run
- ``agent.sustainability.session_count_throttle`` → reflections.agents.session_count_throttle.run
- ``agent.sustainability.failure_loop_detector``  → reflections.agents.failure_loop_detector.run
- ``agent.sustainability.sustainability_digest``  → reflections.agents.system_health_digest.run

New code should import the reflection directly from its per-reflection module.

``send_hibernation_notification`` is NOT a reflection — it is a helper imported
directly by ``agent/agent_session_queue.py`` (the circuit-health hibernation
path). Its one canonical definition now lives in
``reflections.agents.circuit_health_gate`` (issue #2439 deduplication — this
module already imports ``circuit_health_gate.run`` below, so importing the
notification helper from the same module keeps the dependency direction
consistent and avoids a circular import). It is re-exported here so existing
callers (``agent/agent_session_queue.py``, tests) keep working unchanged.
The ``_get_project_key`` / ``_get_redis`` helpers below remain independently
defined in both modules -- they are tiny, side-effect-free env/connection
lookups, not the notification-duplication this issue targets.
"""

import logging
import os

# Re-exports so config/reflections.yaml's historical callable paths still resolve.
from reflections.agents.circuit_health_gate import run as circuit_health_gate
from reflections.agents.circuit_health_gate import (
    send_hibernation_notification,
)
from reflections.agents.failure_loop_detector import run as failure_loop_detector
from reflections.agents.session_count_throttle import run as session_count_throttle
from reflections.agents.session_recovery_drip import run as session_recovery_drip
from reflections.agents.system_health_digest import run as sustainability_digest

logger = logging.getLogger(__name__)

__all__ = [
    "circuit_health_gate",
    "session_recovery_drip",
    "session_count_throttle",
    "failure_loop_detector",
    "sustainability_digest",
    "send_hibernation_notification",
    "_get_project_key",
    "_get_redis",
]


def _get_project_key() -> str:
    """Return the project-scoped Redis key prefix.

    Sources VALOR_PROJECT_KEY from env (injected by worker/bridge plist
    generators). Empty or whitespace-only values fall back to ``"valor"`` so a
    misconfigured ``VALOR_PROJECT_KEY=`` line in ``.env`` does not produce a
    bare ``:sustainability:queue_paused`` key (issue #1171).
    """
    v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
    return v or "valor"


def _get_redis():
    """Return the shared Popoto Redis connection."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


# send_hibernation_notification's one canonical definition now lives in
# reflections.agents.circuit_health_gate (imported above, re-exported via
# __all__) -- see the module docstring for why (issue #2439 dedup).
