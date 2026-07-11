"""Normalized turn-event type names for the HarnessAdapter seam (plan #2000).

Aligned with codex's ``ThreadEvent`` naming (``turn.started``, ``item.*``,
``turn.completed{usage}``, ``turn.failed``) but scoped to exactly what the
session runner consumes for liveness/telemetry — see plan #2000 Rabbit
Holes ("Building a universal event superset"). Do not add a new event type
without a runner-side consumer; every claude stream-json event type is
deliberately NOT modeled here.
"""

from __future__ import annotations

# Fired the moment the harness reports a new (or reused) session id — the
# claude CLI's ``system/init`` event. Carries ``{"handle": <resume handle>
# | None, "raw": <raw init event dict>}``.
#
# MUST be the first handle-bearing event emitted per turn (Race 1, plan
# #2000): the runner persists the resume handle at first sight so a worker
# crash between turn-start and persistence cannot lose it for crash
# auto-resume (#1917).
SESSION_STARTED = "session.started"

# Fired once the subprocess pid is known. Carries ``{"pid": <int>}``.
TURN_SPAWNED = "turn.spawned"

# Fired once per non-empty stdout line from the subprocess (liveness
# heartbeat). No payload.
ITEM_STDOUT = "item.stdout"

# Fired once the subprocess has exited (before result parsing/retries
# resolve). No payload.
TURN_EXITED = "turn.exited"

# Fired exactly once, at the end of ``HarnessAdapter.run_turn()``, carrying
# the turn's usage/cost/exit-shape summary. Carries ``{"usage": dict | None,
# "cost_usd": float | None, "returncode": int | None, "result_event_fired":
# bool | None}``.
TURN_COMPLETED = "turn.completed"
