# SDK Modernization

## Overview

The SDK client uses `claude-agent-sdk` 0.1.35 with programmatic agent
definitions, expanded hooks, and agent-addressable steering.

## Programmatic Agent Registry

- `agent/agent_definitions.py` exports `get_agent_definitions()`
- Defines 3 agents: builder, validator, code-reviewer
- Passed via `agents=` parameter to `ClaudeAgentOptions`
- `.claude/agents/*.md` files remain as fallback for CLI usage
- Programmatic definitions take precedence for SDK sessions

## Expanded Hooks

- `agent/hooks/` package with `build_hooks_config()`
- 4 hook types registered: PreToolUse, PostToolUse, Stop, PreCompact
- **PreToolUse**: blocks writes to sensitive files (.env, credentials)
- **PostToolUse**: watchdog (health check + steering)
- **Stop**: logs session completion
- **PreCompact**: logs context compaction events. The hook snapshots the JSONL transcript, enforces a 5-minute per-session cooldown, retains the last 3 backups per session, and arms the 30-second post-compact nudge guard — see [Compaction Hardening](compaction-hardening.md). The hook never raises.
- All hooks use proper SDK type annotations

## Agent-Addressable Steering

- `target_agent` field on steering queue messages
- Enables inter-agent message routing
- Wire-format only -- no filtering yet

## Files

- `agent/agent_definitions.py`
- `agent/hooks/__init__.py`
- `agent/hooks/pre_tool_use.py`
- `agent/hooks/post_tool_use.py`
- `agent/hooks/stop.py`
- `agent/hooks/pre_compact.py`
- `agent/sdk_client.py`
- `agent/health_check.py`
- `agent/steering.py`
- `pyproject.toml`
