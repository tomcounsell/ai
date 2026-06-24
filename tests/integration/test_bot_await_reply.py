"""Integration test: the agent can invoke `valor-telegram send --await-reply`.

This exercises the REAL agent path (Bash tool -> valor-telegram CLI -> awaiter ->
TelegramMessage store) end-to-end, without a live bridge or bot:

  1. A registered bot is declared in a temp projects.json (PROJECTS_CONFIG_PATH).
  2. We simulate the bridge having recorded the bot's settled reply into the
     history store (what the loop-guard + edit-capture path would do live).
  3. We invoke `python -m tools.valor_telegram send --await-reply --json` as a
     subprocess (as the agent would via Bash) and assert it returns the settled
     reply, including the glued warning footer.

It also asserts the negative gate: --await-reply against an UNREGISTERED id is
refused with a non-zero exit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
_BOT_ID = 8837490628


def _test_db() -> int:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
    if worker_id.startswith("gw"):
        return int(worker_id[2:]) + 1
    return 1


def _subprocess_env(config_path: str) -> dict:
    env = {**os.environ}
    env["REDIS_URL"] = f"redis://127.0.0.1:6379/{_test_db()}"
    env["PROJECTS_CONFIG_PATH"] = config_path
    # Keep the CLI from running the Read-the-Room pass (agent-context only).
    env.pop("VALOR_SESSION_ID", None)
    return env


@pytest.fixture
def temp_config(tmp_path) -> str:
    cfg = {
        "projects": {
            "valor": {
                "name": "Valor AI",
                "machine": "TestMachine",
                "working_directory": str(tmp_path),
                "telegram": {
                    "bots": [
                        {
                            "id": _BOT_ID,
                            "username": "cyndra_staff_bot",
                            "under_test": True,
                            "settle_profile": {
                                "quiet_window_seconds": 1,
                                "default_timeout_seconds": 15,
                            },
                        }
                    ],
                },
            }
        },
        "defaults": {"working_directory": str(tmp_path)},
    }
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(cfg))
    return str(path)


def test_await_reply_returns_settled_reply(temp_config):
    """A pre-recorded bot reply (with footer) is returned by --await-reply --json."""
    from tools.telegram_history import store_message

    footer_answer = "Yep — fixed the publish hook.\n\n⚠️ Could not verify the file landed."
    # Simulate what the bridge records for a registered bot (loop-guard path):
    # an inbound message, no session spawned.
    store_message(
        chat_id=str(_BOT_ID),
        content=footer_answer,
        sender="cyndra_staff_bot",
        message_id=5001,
        # Comfortably after the probe's send_ts (the subprocess sends slightly
        # later than this parent process), so the awaiter's "timestamp >= send_ts"
        # filter keeps it.
        timestamp=time.time() + 3600,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.valor_telegram",
            "send",
            "--chat",
            str(_BOT_ID),
            "--await-reply",
            "--timeout",
            "15",
            "--json",
            "probe: status?",
        ],
        cwd=_PROJECT_ROOT,
        env=_subprocess_env(temp_config),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"stderr={result.stderr}\nstdout={result.stdout}"
    payload = json.loads(result.stdout)
    assert payload["settled"] is True
    assert "fixed the publish hook" in payload["reply"]["settled_text"]
    # The glued footer is preserved (a test signal), not stripped.
    assert "⚠️" in payload["reply"]["settled_text"]
    assert payload["reply"]["footer_present"] is True


def test_await_reply_refuses_unregistered_id(temp_config):
    """--await-reply against an id NOT in telegram.bots[] exits non-zero."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.valor_telegram",
            "send",
            "--chat",
            "111222333",  # not registered
            "--await-reply",
            "hi",
        ],
        cwd=_PROJECT_ROOT,
        env=_subprocess_env(temp_config),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "registered bot" in result.stderr.lower()
