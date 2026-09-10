"""Argv-level tests for the session-local MCP config merge (plan #2001 Task 3).

``_merge_mcp_config`` is the only argv surface the Codex dev lane adds to
the top-level Claude turn: unflagged turns (``mcp_config=None``) stay
byte-identical, flagged turns splice exactly one ``--mcp-config=`` entry
(merging into a belt-resolved entry when present, never a second flag)
plus ``--strict-mcp-config``.
"""

from __future__ import annotations

import json

from agent.session_runner.harness.base import TurnRequest
from agent.session_runner.harness.claude import _merge_mcp_config


def _lane_config():
    return {
        "mcpServers": {
            "codex_dev": {
                "command": "python3",
                "args": ["-m", "mcp_servers.codex_dev_server"],
                "env": {"AGENT_SESSION_ID": "agent-id-1"},
            }
        }
    }


def test_none_config_leaves_argv_byte_identical():
    argv = ["claude", "-p", "--output-format=stream-json"]
    before = list(argv)
    _merge_mcp_config(argv, None)
    assert argv == before


def test_config_appends_single_pair_when_no_entry_present():
    argv = ["claude", "-p"]
    _merge_mcp_config(argv, _lane_config())
    assert argv[:2] == ["claude", "-p"]
    assert argv[-2].startswith("--mcp-config=")
    assert argv[-1] == "--strict-mcp-config"
    assert sum(p.startswith("--mcp-config=") for p in argv) == 1
    payload = json.loads(argv[-2][len("--mcp-config=") :])
    assert payload["mcpServers"]["codex_dev"]["env"] == {"AGENT_SESSION_ID": "agent-id-1"}


def test_config_merges_into_existing_entry_without_second_flag():
    argv = ["claude", '--mcp-config={"mcpServers":{"belt":{}}}']
    _merge_mcp_config(argv, _lane_config())
    assert sum(p.startswith("--mcp-config=") for p in argv) == 1
    payload = json.loads(argv[1][len("--mcp-config=") :])
    assert set(payload["mcpServers"]) == {"belt", "codex_dev"}
    # The merge branch stays strict too: ambient global MCP servers stay
    # hidden from flagged turns even when a belt entry already exists.
    assert "--strict-mcp-config" in argv


def test_merge_branch_does_not_duplicate_strict_flag():
    argv = ["claude", '--mcp-config={"mcpServers":{"belt":{}}}', "--strict-mcp-config"]
    _merge_mcp_config(argv, _lane_config())
    assert argv.count("--strict-mcp-config") == 1


def test_malformed_config_is_ignored():
    argv = ["claude", "-p"]
    _merge_mcp_config(argv, {"mcpServers": "not-a-mapping"})
    assert argv == ["claude", "-p"]


def test_turn_request_defaults_to_no_mcp_config():
    req = TurnRequest(message="hi", working_dir="/tmp")
    assert req.mcp_config is None
