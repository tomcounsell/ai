"""Every harness ``claude`` spawn carries the worker ownership marker (#3592).

The worker's orphan reapers signal a process only when its environment proves
this system spawned it. Session turns also carry ``AGENT_SESSION_ID``; these
tests pin the marker on the spawns that have no session to name (drafter calls
and the startup health probe), including against a caller env that tries to
override it.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.sdk_client import get_response_via_harness, verify_harness_health
from agent.session_runner.hook_edge import HARNESS_OWNER_ENV


class _CapturedError(Exception):
    pass


@pytest.mark.asyncio
async def test_sessionless_harness_spawn_carries_owner_marker(monkeypatch):
    captured = {}

    async def fake_subprocess(cmd, working_dir, proc_env, **kwargs):
        captured["env"] = dict(proc_env)
        raise _CapturedError

    monkeypatch.setattr(
        "agent.session_runner.harness.claude._run_harness_subprocess", fake_subprocess
    )

    with pytest.raises(_CapturedError):
        await get_response_via_harness(
            message="hello",
            working_dir="/tmp",
            env={HARNESS_OWNER_ENV: ""},
        )

    assert captured["env"][HARNESS_OWNER_ENV] == str(os.getpid())


@pytest.mark.asyncio
async def test_health_probe_spawn_carries_owner_marker():
    lines = [
        (json.dumps({"type": "system", "apiKeySource": "none"}) + "\n").encode(),
        b"",
    ]
    with (
        patch("shutil.which", return_value="/usr/local/bin/claude"),
        patch("asyncio.create_subprocess_exec") as mock_exec,
    ):
        mock_proc = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(side_effect=lines)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_exec.return_value = mock_proc

        assert await verify_harness_health("claude-cli") is True

    assert mock_exec.call_args.kwargs["env"][HARNESS_OWNER_ENV] == str(os.getpid())
