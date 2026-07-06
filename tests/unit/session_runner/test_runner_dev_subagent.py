"""Dev subagent definition + PM prime contract + resume seam (task 2).

D1-amended topology: Dev is a well-defined subagent the PM spawns inside its
own turn and continues across turns (and, after build-resume lands, across
process restarts). The continuation/steering protocol must be BAKED INTO the
agent definition at authoring time — post-hoc overrides are refused by the
harness (spike #1928, gotcha 1). These tests pin that contract:

* ``.claude/agents/dev.md`` exists, is named ``dev``, and carries the
  continuation, preemption-recovery, steering, rails, and report-to-PM
  clauses.
* The PM prime instructs: spawn ``dev`` on first need, report its agent id,
  continue the SAME agent later, relay steers verbatim with ``[STEER]``;
  the retired ``[/dev]`` routing token is gone.
* The runner exposes the ResumeContext seam (four scalars) that build-resume
  (task 3) consumes — including ``dev_agent_id`` for same-agent continuation
  across a restart.
"""

from __future__ import annotations

import pathlib

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.runner import ResumeContext, SessionRunner
from tests.unit.session_runner.test_runner_turns import (
    FakeSession,
    ScriptedDriver,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEV_AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "dev.md"
PM_PRIME_PATH = REPO_ROOT / ".claude" / "commands" / "granite" / "prime-pm-role.md"


# --------------------------------------------------------------------------
# Dev agent definition contract
# --------------------------------------------------------------------------


def test_dev_agent_definition_exists_and_is_named_dev():
    assert DEV_AGENT_PATH.is_file(), "dev agent definition missing"
    text = DEV_AGENT_PATH.read_text(encoding="utf-8")
    assert text.startswith("---")
    frontmatter = text.split("---", 2)[1]
    assert "name: dev" in frontmatter


def test_dev_agent_bakes_in_continuation_contract():
    """Continuation semantics live in the definition itself (gotcha 1) —
    the PM cannot bolt them on later."""
    body = DEV_AGENT_PATH.read_text(encoding="utf-8")
    # Continuable across turns and process restarts.
    assert "continuable" in body.lower()
    assert "process restart" in body.lower()
    # Preemption recovery: re-check ground truth before trusting prior state.
    assert "preempted" in body.lower()
    assert "git status" in body
    # Steering protocol: [STEER] prefix, course-correction not redefinition.
    assert "[STEER]" in body
    assert "never redefine" in body.lower()


def test_dev_agent_bakes_in_rails_and_reporting():
    body = DEV_AGENT_PATH.read_text(encoding="utf-8")
    # Safety rails inlined (the definition must be self-contained).
    assert "main" in body and "Never push code directly" in body
    assert "Co-Authored-By" in body  # co-author prohibition
    # Report-to-PM contract: Dev never addresses the human.
    assert "report to the pm" in body.lower()
    assert "send messages to the human directly" in body.lower()
    # SDLC ownership with gates.
    assert "/do-plan-critique" in body
    assert "/do-pr-review" in body
    assert "/do-merge" in body


# --------------------------------------------------------------------------
# PM prime contract: spawn once, report id, continue the SAME agent
# --------------------------------------------------------------------------


def test_pm_prime_spawns_dev_on_first_need_and_reports_agent_id():
    body = PM_PRIME_PATH.read_text(encoding="utf-8")
    assert "On first need" in body
    assert "agent id" in body.lower()
    # Continue the SAME agent on later turns; never a second dev.
    assert "SAME" in body
    assert "Never spawn a second dev" in body
    # Steering relayed verbatim to the same agent.
    assert "[STEER]" in body


def test_pm_prime_retires_dev_routing_token():
    """The [/dev] routing token and harness suffixes are gone; only
    [/user] and [/complete] remain."""
    body = PM_PRIME_PATH.read_text(encoding="utf-8")
    assert "[/dev]" not in body
    assert "[/dev:" not in body
    assert "[/user]" in body
    assert "[/complete]" in body
    # The documented classifier regex matches the simplified table.
    assert r"^\[/(user|complete)\]\s*$" in body


# --------------------------------------------------------------------------
# Resume seam (consumed by build-resume, task 3)
# --------------------------------------------------------------------------


def test_resume_context_carries_the_four_scalars():
    ctx = ResumeContext(
        claude_session_id="uuid-1",
        dev_agent_id="agent-a1b2c3",
        runner_cwd="/tmp/wd",
        claude_version="2.1.201",
    )
    assert ctx.claude_session_id == "uuid-1"
    assert ctx.dev_agent_id == "agent-a1b2c3"
    assert ctx.runner_cwd == "/tmp/wd"
    assert ctx.claude_version == "2.1.201"
    # All four default to None (cold start).
    assert ResumeContext() == ResumeContext(None, None, None, None)


async def test_runner_accepts_and_stores_resume_context():
    """The runner holds the ResumeContext for build-resume's consumption —
    the dev_agent_id survives runner construction so the SAME dev agent can
    be continued after a worker restart."""
    session = FakeSession()
    adapter = SessionRunnerAdapter(
        session,
        "test-proj",
        "telegram",
        resolve_callbacks=lambda pk, t: (lambda c, p, r, s: None, None),
    )
    ctx = ResumeContext(claude_session_id="uuid-9", dev_agent_id="agent-dev-7")
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        driver=ScriptedDriver(["[/complete]\ndone"]),
        resume=ctx,
        steering_pop_fn=lambda: [],
    )
    assert runner._resume is ctx
    assert runner._resume.dev_agent_id == "agent-dev-7"
    summary = await runner.run("continue the work")
    assert summary.exit_reason == "pm_complete"
