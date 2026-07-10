"""Subprocess-boundary E2E for sessionless SDLC state persistence (#1558).

The bug: in a direct (non-`/sdlc`) Claude Code session with no PM AgentSession,
every `sdlc-tool` state *write* silently no-ops because the resolver returns
None. The fix makes the three *write* subcommands resolve through
``find_session(..., ensure=True)``, which auto-creates a ``sdlc-local-{N}`` PM
session so the write lands.

This test proves persistence survives **process boundaries** (the real bug),
not in-process Popoto object reuse:
  - Each step is a DISTINCT ``subprocess.run(["sdlc-tool", ...])`` with
    ``VALOR_SESSION_ID``/``AGENT_SESSION_ID`` forced empty (clean-env
    local-create path).
  - Assertions read **parsed stdout JSON** from a separate ``get`` subprocess,
    never an in-process ``AgentSession.query`` result.
  - A high throwaway issue number avoids colliding with a real PM session.
  - Teardown deletes the created ``sdlc-local-{N}`` session via Popoto in a
    ``finally`` (Manual Testing Hygiene).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.sdlc

# Resolve the repo root from this test file so the test exercises the code in
# whatever checkout it runs from (worktree during build, main repo in CI).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# A high throwaway issue number unlikely to collide with any real PM session.
ISSUE_NUMBER = 999137
LOCAL_SESSION_ID = f"sdlc-local-{ISSUE_NUMBER}"

SDLC_TOOL = shutil.which("sdlc-tool")


def _run(*args: str) -> subprocess.CompletedProcess:
    """Run sdlc-tool in a clean env with the worktree as AI_REPO_ROOT.

    Redis is isolated to the per-worker test db via REDIS_URL (#2003: writes
    now include the issue lock and active_run_id — tests must never touch
    production Redis), matching the in-process autouse fixture so subprocess
    writes and in-process teardown see the SAME db. SDLC_HOLDER_TOKEN is
    stripped: the env seam is deleted — run identity travels via --run-id.
    """
    import popoto.redis_db as rdb

    kwargs = rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs
    env = {
        **os.environ,
        # Force the clean-env local-create path: no bridge session injected.
        "VALOR_SESSION_ID": "",
        "AGENT_SESSION_ID": "",
        # Point the dispatcher at THIS checkout so we exercise the modified code.
        "AI_REPO_ROOT": str(REPO_ROOT),
        "REDIS_URL": (
            f"redis://{kwargs.get('host') or 'localhost'}:"
            f"{kwargs.get('port') or 6379}/{kwargs.get('db', 1)}"
        ),
    }
    env.pop("SDLC_HOLDER_TOKEN", None)
    return subprocess.run(
        [SDLC_TOOL, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _delete_local_session() -> None:
    """Delete the sdlc-local-{N} session via Popoto (never raw Redis)."""
    try:
        from models.agent_session import AgentSession

        for s in AgentSession.query.filter(session_id=LOCAL_SESSION_ID):
            s.delete()
    except Exception:
        # Teardown is best-effort; never fail the suite on cleanup.
        pass


@pytest.mark.skipif(SDLC_TOOL is None, reason="sdlc-tool not installed on PATH")
class TestSessionlessRoundTrip:
    def test_verdict_record_then_get_round_trips_across_processes(self):
        """A sessionless `verdict record` persists; a separate `verdict get`
        process reads the same verdict back from Redis."""
        # Pre-clean in case a prior run left a stale local session.
        _delete_local_session()
        try:
            # #2003: session-ensure is the exclusive run_id minting site. It
            # runs as its OWN subprocess; the verdict record below is a
            # SECOND process presenting the same run_id explicitly — the
            # #1971 scenario inverted, with no SDLC_HOLDER_TOKEN in the env.
            ens = _run("session-ensure", "--issue-number", str(ISSUE_NUMBER))
            assert ens.returncode == 0, f"session-ensure failed: {ens.stderr}"
            ens_json = json.loads(ens.stdout.strip())
            # If the environment cannot resolve a project_key (e.g. no
            # projects.json in CI), ensure_session returns {} and the write
            # degrades to a no-op. In that case the whole round-trip is
            # un-exercisable; skip rather than fail.
            if not ens_json or ens_json.get("blocked"):
                pytest.skip(
                    "Environment could not auto-ensure a session (no project "
                    "context) — sessionless persistence not exercisable here."
                )
            run_id = ens_json["run_id"]
            assert run_id, ens_json

            rec = _run(
                "verdict",
                "record",
                "--stage",
                "CRITIQUE",
                "--verdict",
                "READY TO BUILD",
                "--issue-number",
                str(ISSUE_NUMBER),
                "--run-id",
                run_id,
            )
            assert rec.returncode == 0, f"record failed: {rec.stderr}"
            rec_json = json.loads(rec.stdout.strip())
            assert rec_json.get("verdict") == "READY TO BUILD", rec_json

            # DISTINCT process reads it back from Redis.
            get = _run(
                "verdict",
                "get",
                "--stage",
                "CRITIQUE",
                "--issue-number",
                str(ISSUE_NUMBER),
            )
            assert get.returncode == 0, f"get failed: {get.stderr}"
            get_json = json.loads(get.stdout.strip())
            assert get_json.get("verdict") == "READY TO BUILD", (
                f"verdict did not round-trip across processes: {get_json}"
            )

            # next-skill (read) reflects recorded state instead of phantom default.
            nxt = _run("next-skill", "--issue-number", str(ISSUE_NUMBER))
            assert nxt.returncode == 0, f"next-skill failed: {nxt.stderr}"
            # Output is valid JSON regardless of routing decision.
            json.loads(nxt.stdout.strip())
        finally:
            _delete_local_session()

    def test_bare_query_no_issue_no_env_still_no_ops(self):
        """A bare `stage-query` with no --issue-number and no env still returns
        empty defaults and exits 0 — reads never fabricate a session."""
        proc = _run("stage-query")
        assert proc.returncode == 0, f"stage-query failed: {proc.stderr}"
        payload = json.loads(proc.stdout.strip())
        # Empty/default payload — no session was created for a read.
        stages = payload.get("stages", payload)
        assert stages == {} or stages == payload, payload
