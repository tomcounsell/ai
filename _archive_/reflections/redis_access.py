"""Canonical Redis access helpers for the self-healing reflection modules.

``get_project_key()`` and ``get_redis()`` used to exist as six byte-identical
private copies — one in the now-deleted ``agent/sustainability.py`` shim and one
in each of the five ``reflections/agents/*.py`` reflection modules — plus a pair
of shim-delegating wrappers in ``reflections/stall_advisory.py`` that did
nothing but forward to the shim's copy. Copies drift, and
``tests/unit/test_default_project_key_consistency.py`` was written specifically
as a guardrail against that drift — it asserts the writer-side
``DEFAULT_PROJECT_KEY`` still agrees with the reader-side fallback. That test
policed a symptom; centralizing the pair here removes the cause.

The names are public (no leading underscore) because they are now a deliberate
shared API across those six consumers rather than module-private helpers.

``get_project_key()`` preserves the issue #1171 fallback semantics exactly: the
``VALOR_PROJECT_KEY`` env value is stripped, and an empty or whitespace-only
value falls back to ``"valor"`` so a misconfigured ``VALOR_PROJECT_KEY=`` line
in ``.env`` cannot produce a bare ``:sustainability:queue_paused`` key.

Scope note: three further ``_get_redis`` definitions live outside this module's
consumer set (``reflections/utilities.py``, ``reflections/docs_auditor.py``,
``agent/steering.py``). They serve different consumers and are deliberately not
folded in here.
"""

import os

__all__ = ["get_project_key", "get_redis"]


def get_project_key() -> str:
    """Return the project-scoped Redis key prefix.

    Sources VALOR_PROJECT_KEY from env (injected by worker/bridge plist
    generators). Empty or whitespace-only values fall back to ``"valor"`` so a
    misconfigured ``VALOR_PROJECT_KEY=`` line in ``.env`` does not produce a
    bare ``:sustainability:queue_paused`` key (issue #1171).
    """
    v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
    return v or "valor"


def get_redis():
    """Return the shared Popoto Redis connection.

    The ``popoto`` import stays inside the function body on purpose: hoisting it
    to module scope would open a Redis connection at import time for every
    reflection module that imports this one.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB
