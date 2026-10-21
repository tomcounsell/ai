"""Orchestration (docs/plans/orchestration.md): the web reached only through
Valor's web agent, subagents to any depth, and the Brief in every subagent."""

import json
import subprocess
import sys
from pathlib import Path

import psycopg

from core import guards
from core.settings import VALOR_PLUGIN, settings
from harnesses import claude_code

HOOK = Path(VALOR_PLUGIN) / "hooks" / "web_only.py"


def hook(call: dict) -> dict | None:
    done = subprocess.run(
        [sys.executable, HOOK], input=json.dumps(call), capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout) if done.stdout.strip() else None


def test_only_the_web_agent_reaches_the_web():
    assert hook({"tool_name": "WebFetch", "agent_type": "valor:web"}) is None
    # A call the hook cannot read is denied, and a hook that fails at all blocks (exit 2).
    unread = subprocess.run(
        [sys.executable, HOOK], input="not json", capture_output=True, text=True, check=True
    )
    assert json.loads(unread.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    command = json.loads((Path(VALOR_PLUGIN) / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"][0]
    failed = command["hooks"][0]["command"].replace("${CLAUDE_PLUGIN_ROOT}", "/nonexistent")
    assert (
        subprocess.run(
            ["sh", "-c", failed], input="{}", capture_output=True, text=True, check=False
        ).returncode
        == 2
    )
    for caller in ({}, {"agent_type": "general-purpose"}, {"agent_type": "web"}):
        out = hook({"tool_name": "WebSearch", **caller})["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny" and "valor:web" in out["permissionDecisionReason"]
    front = (Path(VALOR_PLUGIN) / "agents" / "web.md").read_text().split("---")[1]
    tools = next(line for line in front.splitlines() if line.startswith("tools:"))
    assert {t.strip() for t in tools.removeprefix("tools:").split(",")} == {"WebFetch", "WebSearch"}
    registered = json.loads((Path(VALOR_PLUGIN) / "hooks" / "hooks.json").read_text())["hooks"]
    assert registered["PreToolUse"][0]["matcher"] == "WebFetch|WebSearch"


def test_the_web_guard_is_ledgered_with_its_grant_and_expiry(dsn):
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE type = 'guard.granted' ORDER BY payload->>'guard_id'"
        ).fetchall()
    by_id = {r[0]["guard_id"]: r[0] for r in rows}
    assert set(by_id) == {g["guard_id"] for g in guards.SEEDED}
    web = by_id["web.agent_only"]
    assert (web["granted_at"], web["expires"], web["mission_items"]) == ("2026-10-16", "2027-01-14", [4])
    assert "#3648" in web["incident"] and web["provenance"]["at"] == "2026-10-16"
    room = by_id["update.room"]
    assert (room["granted_at"], room["expires"], room["mission_items"]) == ("2026-10-20", "2027-01-18", [1])
    assert room["source"] == room["provenance"]["via"] == "docs/plans/update.md, Grant"
    assert room["firing_record"] is False and room["provenance"]["at"] == "2026-10-20"
    own = ("web.agent_only", "update.room")
    assert all(by_id[g]["expires"] == guards.EXPIRES.isoformat() for g in by_id if g not in own)


def test_a_turn_loads_valors_plugin_first_and_gives_every_subagent_the_brief():
    assert settings.plugin_dirs[0] == VALOR_PLUGIN
    made = claude_code.workspace_turn("p", cwd=".", harness={"sandbox_profile": "turn"})
    argv_env = made("http://h:1", "the brief\nwith lines", "t")
    argv = argv_env.argv
    assert argv[argv.index("--plugin-dir") + 1] == VALOR_PLUGIN
    assert argv_env.env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == claude_code.SPAWN_DEPTH
    start = json.loads(argv_env.env["VALOR_SUBAGENT_START"])["hookSpecificOutput"]
    assert start["hookEventName"] == "SubagentStart"
    assert start["additionalContext"].endswith("\n\nthe brief\nwith lines")
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": False}
    assert "--disallowedTools" not in argv


def test_every_subagent_but_the_web_agent_opens_with_the_brief():
    start = Path(VALOR_PLUGIN) / "hooks" / "subagent_start.py"

    def opens(agent_type: str) -> str:
        env = {"VALOR_SUBAGENT_START": claude_code.subagent_start("Brief ü")}
        call = json.dumps({"hook_event_name": "SubagentStart", "agent_type": agent_type})
        return subprocess.run(
            [sys.executable, start], input=call, env=env, capture_output=True, text=True, check=True
        ).stdout

    assert json.loads(opens("general-purpose"))["hookSpecificOutput"]["additionalContext"].endswith("Brief ü")
    assert opens("valor:web") == ""
