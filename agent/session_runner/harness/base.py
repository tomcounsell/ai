"""HarnessAdapter protocol: the seam behind which all turn-based headless
CLI knowledge lives (plan #2000, Phase 2 of ``harness-cross-compat.md``).

Before this seam, the headless session runner drove ``claude -p`` via a
bare async function (``get_response_via_harness``) welded into
``agent/sdk_client.py``: argv assembly, stream-json parsing, and resume-id
capture all lived inline, with no seam to swap the subprocess or normalize
its output. ``HarnessAdapter`` is the protocol every concrete adapter (today
only ``agent.session_runner.harness.claude.ClaudeHarnessAdapter``)
implements; the runner consumes only ``TurnRequest``/``TurnResult``/
``TurnEvent`` — never claude-specific argv or stream-json shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent.session_runner.router import ExitReason


@dataclass
class TurnEvent:
    """One normalized turn-lifecycle event.

    ``type`` is one of the fixed vocabulary in ``harness/events.py``
    (``session.started`` / ``turn.spawned`` / ``item.stdout`` /
    ``turn.exited`` / ``turn.completed``). ``data`` carries the
    event-specific payload documented alongside each constant.
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnRequest:
    """Normalized input to :meth:`HarnessAdapter.run_turn`.

    Mirrors the pre-extraction ``get_response_via_harness()`` keyword
    arguments 1:1 — this is a behavior-preserving extraction (plan #2000
    Task 2.2), not a redesign of the turn contract.

    ``json_schema`` (plan #2000 Task 2.3) is passed through to the harness's
    ``--json-schema`` flag when set; ``None`` (the default) omits the flag
    entirely — non-role harness callers (message drafter, probes) are
    unaffected.
    """

    message: str
    working_dir: str
    env: dict[str, str] | None = None
    prior_uuid: str | None = None
    session_id: str | None = None
    full_context_message: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    settings_path: str | None = None
    metered: bool = False
    role: str | None = None
    start_new_session: bool = False
    harness_cmd: list[str] | None = None
    json_schema: dict[str, Any] | None = None


@dataclass
class TurnResult:
    """Normalized return type for :meth:`HarnessAdapter.run_turn`.

    The ex-T2.4 ``HarnessResult`` (plan #2000 Definitions table).
    ``exit_reason`` reuses #2004's :class:`ExitReason` StrEnum — no
    parallel taxonomy. Adapters that cannot classify a failure from their
    own signals leave ``exit_reason`` ``None`` and let the caller classify
    (e.g. a caller-side timeout wrapping ``run_turn()``).
    """

    resume_handle: str | None = None
    final_text: str = ""
    structured_output: dict[str, Any] | None = None
    events: list[TurnEvent] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None
    returncode: int | None = None
    result_event_fired: bool | None = None
    exit_reason: ExitReason | None = None


@runtime_checkable
class HarnessAdapter(Protocol):
    """Drives one turn-based headless CLI subprocess per call.

    Implementations own all subprocess-specific knowledge (argv assembly,
    env, output parsing). Callers get back a normalized :class:`TurnResult`
    and — via the optional ``on_event`` callback — a *live* stream of
    normalized :class:`TurnEvent` objects as they occur, so the caller can
    persist the resume handle the instant it is known (Race 1: a worker
    crash between turn-start and handle persistence must not lose the
    resume handle for crash auto-resume, #1917). ``on_event`` fires
    synchronously, in-line with the subprocess's own output — never only
    after ``run_turn()`` returns.
    """

    async def run_turn(
        self,
        request: TurnRequest,
        *,
        on_event: Callable[[TurnEvent], None] | None = None,
    ) -> TurnResult: ...
