# SDK Modernization

## Overview

Upgraded `claude-agent-sdk` from 0.1.27 to 0.1.35 with programmatic agent definitions, expanded hooks, and agent-addressable steering.

## Version Upgrade

- Bumped from 0.1.27 to 0.1.35
- Key fixes: ARG_MAX fix (command-line length overflow), AssistantMessage.error fix (proper error attribute handling)

## Programmatic Agent Registry

- `agent/agent_definitions.py` exports `get_agent_definitions()`
- Defines 3 agents: builder, validator, code-reviewer
- Passed via `agents=` parameter to `ClaudeAgentOptions`
- `.claude/agents/*.md` files remain as fallback for CLI usage
- Programmatic definitions take precedence for SDK sessions

## Expanded Hooks

- New `agent/hooks/` package with `build_hooks_config()`
- 4 hook types registered: PreToolUse, PostToolUse, Stop, PreCompact
- **PreToolUse**: blocks writes to sensitive files (.env, credentials)
- **PostToolUse**: existing watchdog (health check + steering)
- **Stop**: logs session completion
- **PreCompact**: logs context compaction events. Since issue #1127 the hook also snapshots the JSONL transcript, enforces a 5-minute per-session cooldown, retains the last 3 backups per session, and arms the 30-second post-compact nudge guard — see [Compaction Hardening](compaction-hardening.md). The hook never raises.
- All hooks use proper SDK type annotations
- A `SubagentStop` hook was originally registered here as well; it was stripped to logging-only in the Phase 5 harness migration and then deleted entirely in issue #1024 once the SDK execution path was confirmed unreachable.

## Agent-Addressable Steering

- `target_agent` field added to steering queue messages
- Enables future inter-agent message routing
- Wire-format only -- no filtering yet

## Files Changed

- `agent/agent_definitions.py` (new)
- `agent/hooks/__init__.py` (new)
- `agent/hooks/pre_tool_use.py` (new)
- `agent/hooks/post_tool_use.py` (new)
- `agent/hooks/stop.py` (new)
- `agent/hooks/pre_compact.py` (new)
- `agent/sdk_client.py` (modified)
- `agent/health_check.py` (modified)
- `agent/steering.py` (modified)
- `pyproject.toml` (modified)
