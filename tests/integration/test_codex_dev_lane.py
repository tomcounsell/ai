"""Live lifecycle probes for the Codex dev lane (plan #2001 Task 5).

Runs REAL ``codex exec`` turns on this provisioned machine (codex-cli
0.154.0, ChatGPT login) through the REAL MCP handler
(``codex_dev_run``) with a REAL session row: first turn opens a thread,
second turn resumes it. Asserts stable thread id, count 1→2, the
PM-visible harness/model/turns/usage attribution, and one Task 3
schema-of-record ``"ok"`` evidence line per executed turn. The count of
``"ok"`` lines is ``live_probe_pass_count``; the test writes artifact
``data/codex_live_probes.json`` (the Task 4b gate record).

Safety: every turn runs with its working_dir in a scratch git repo under
``tmp_path`` (preflight requires a repo root), and the instruction
forbids file actions — stray files can only land in tmp. The session row
uses the ``test-2001`` project prefix and is deleted afterward.

When the machine is not provisioned (preflight fails) the probe SKIPS
with the preflight reason — the negative evidence the plan's
probe-failure disposition records on #2001.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

PROBE_INSTRUCTION = (
    "This is a connectivity probe, not a work request. "
    "Do not create, modify, or delete any files. Do not run any commands. "
    "Your entire final message must be exactly this JSON and nothing else: "
    '{"report": "PROBE-OK", "complete": true}'
)

ARTIFACT_PATH = Path("data") / "codex_live_probes.json"


def _scratch_repo(tmp_path: Path) -> str:
    repo = tmp_path / "probe-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "probe@test-2001.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "probe"], cwd=repo, check=True)
    (repo / "README.md").write_text("probe scratch repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "probe base"], cwd=repo, check=True)
    return str(repo)


def test_codex_dev_lane_live_lifecycle(tmp_path, monkeypatch):
    import agent.codex_turn_log as log_mod
    import mcp_servers.codex_dev_server as server
    from agent.session_runner.harness.codex import preflight_codex
    from models.agent_session import AgentSession

    worktree = _scratch_repo(tmp_path)
    version, preflight_error = preflight_codex(sandbox="workspace-write", worktree=worktree)
    if preflight_error is not None:
        pytest.skip(f"codex not provisioned on this machine: {preflight_error}")

    lane = tmp_path / "probe-lane.jsonl"
    monkeypatch.setattr(log_mod, "lane_path_for", lambda sid: lane)

    session = AgentSession.create(
        session_id=f"live-probe-{uuid.uuid4().hex[:12]}",
        session_type="eng",
        project_key="test-2001",
        working_dir=worktree,
        status="pending",
        chat_id="999",
        message_text="live probe",
        sender_name="probe",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
        dev_harness="codex",
        codex_turn_count=0,
        dev_lane_fence=f"fence-probe-{uuid.uuid4().hex[:8]}",
    )
    monkeypatch.setenv("AGENT_SESSION_ID", str(session.id))
    try:
        first = server.codex_dev_run(PROBE_INSTRUCTION)
        assert first["ok"] is True, f"first live turn failed: {first['error']}"
        assert "PROBE-OK" in (first["report"] or "")
        assert "[dev harness=codex" in (first["report"] or "")
        assert first["turn_count"] == 1
        thread_id = first["thread_id"]
        assert thread_id, "no thread id persisted from the live turn"

        second = server.codex_dev_run(PROBE_INSTRUCTION)
        assert second["ok"] is True, f"resume turn failed: {second['error']}"
        assert second["thread_id"] == thread_id, "thread id not stable across resume"
        assert second["turn_count"] == 2

        lines = [json.loads(line) for line in lane.read_text().splitlines()]
        ok_lines = [r for r in lines if r.get("outcome") == "ok"]
        live_probe_pass_count = len(ok_lines)
        assert live_probe_pass_count >= 2
        for record in ok_lines:
            assert set(record) == {"thread_id", "turn_count", "outcome", "usage", "wall_clock_ms"}
            assert record["thread_id"] == thread_id
        assert [r["turn_count"] for r in ok_lines] == [1, 2]

        artifact = {
            "plan": "codex-exec-dev-lane",
            "issue": 2001,
            "codex_live_probes": live_probe_pass_count,
            "live_probe_pass_count": live_probe_pass_count,
            "thread_id": thread_id,
            "codex_version": version,
            "lane_file": str(lane),
            "recorded_at": datetime.now(tz=UTC).isoformat(),
        }
        ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2) + "\n")
    finally:
        try:
            session.delete()
        except Exception:  # noqa: BLE001 -- test-DB fixture flush is the backstop
            pass
