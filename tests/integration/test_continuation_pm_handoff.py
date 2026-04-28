"""Integration test for the dev → PM continuation handoff (issue #1195).

End-to-end: a terminal parent PM and a completed dev session are written to
Redis (via the autouse ``redis_test_db`` fixture). ``_handle_dev_session_completion``
is invoked; its real ``_create_continuation_pm`` path runs and saves a new
PM session. We then assert:

* The continuation PM exists with ``parent_agent_session_id`` pointing at the
  parent and the spawn contract enforced (``session_id`` follows the
  ``{parent.session_id}_cont{depth}`` chain pattern, ``working_dir`` is
  populated).
* The executor entry guard does NOT fire for that session (i.e. neither
  ``working_dir`` nor ``session_id`` is ``None``), confirming end-to-end that
  the spawn-site fix produces sessions the worker can actually execute.

This test is the regression guard for the silent-death bug at
``agent/session_completion.py:358`` where the raw permissive
``_AgentSession.create(...)`` left both fields ``None`` and dev → PM handoffs
died at the executor's ``Path(session.working_dir)`` line.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession


@pytest.mark.asyncio
async def test_dev_completion_spawns_executable_continuation_pm(redis_test_db):
    """Dev session completion → continuation PM with spawn-contract fields.

    The continuation PM must:
      * exist as a child of the parent
      * have a non-``None`` ``session_id`` matching ``{parent}_cont{depth}``
      * have a non-``None`` ``working_dir``
      * pass the executor guard's preconditions (both fields populated)
    """
    from agent.agent_session_queue import _handle_dev_session_completion

    # Parent: terminal PM with realistic working_dir.
    parent = AgentSession.create(
        session_id="pm-handoff-int-001",
        session_type="pm",
        project_key="test",
        working_dir="/tmp",
        status="completed",
        chat_id="999",
        message_text="Run SDLC on issue #1195 (issues/1195)",
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
        continuation_depth=0,
    )

    # Dev session: terminal child, completed BUILD stage.
    dev = AgentSession.create(
        session_id="dev-handoff-int-001",
        session_type="dev",
        project_key="test",
        working_dir="/tmp",
        status="completed",
        chat_id="999",
        message_text="Stage: BUILD\nImplement fix for issue #1195 (issues/1195)",
        parent_agent_session_id=parent.agent_session_id,
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )

    # Steer is rejected because parent is terminal — drives the
    # continuation-PM fallback path.
    with (
        patch(
            "agent.agent_session_queue.steer_session",
            return_value={
                "success": False,
                "session_id": parent.session_id,
                "error": "Session is in terminal status 'completed' — steering rejected",
            },
        ),
        patch("agent.agent_session_queue._extract_issue_number", return_value=1195),
    ):
        await _handle_dev_session_completion(
            session=parent,
            agent_session=dev,
            result="BUILD complete. PR created.",
        )

    # Locate the continuation PM via the parent FK.
    pm_children = [
        c
        for c in AgentSession.query.filter(parent_agent_session_id=parent.agent_session_id)
        if c.session_type == "pm"
    ]
    assert len(pm_children) == 1, (
        f"Expected exactly one continuation PM, got {len(pm_children)}: "
        f"{[c.session_id for c in pm_children]}"
    )
    cont = pm_children[0]

    # --- Spawn contract (issue #1195) ---
    assert cont.status == "pending"
    assert cont.session_id is not None
    assert cont.working_dir is not None
    # session_id chain pattern: {parent.session_id}_cont{depth}
    assert cont.session_id == f"{parent.session_id}_cont1"
    # working_dir falls back to the resolved path (parent project_config absent
    # → projects.json absent for "test" → os.getcwd()). The exact value is
    # implementation-detail; the contract is "non-None and non-empty".
    assert cont.working_dir
    assert cont.continuation_depth == 1

    # --- Executor-guard preconditions hold ---
    # The guard at agent/session_executor.py short-circuits when either
    # field is None. The whole point of the spawn-site fix is that this
    # guard never fires for legitimately-spawned continuation PMs.
    assert cont.working_dir is not None and cont.session_id is not None, (
        "Executor guard would fire for this continuation PM — spawn contract violated."
    )
