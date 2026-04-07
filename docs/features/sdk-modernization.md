# SDK Modernization

## Overview

Upgraded `claude-agent-sdk` from 0.1.27 to 0.1.35 with programmatic agent definitions, expanded hooks, cost budgeting, and agent-addressable steering.

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
- 5 hook types registered: PreToolUse, PostToolUse, Stop, SubagentStop, PreCompact
- **PreToolUse**: blocks writes to sensitive files (.env, credentials)
- **PostToolUse**: existing watchdog (health check + steering)
- **Stop**: logs session completion
- **SubagentStop**: logs subagent completion
- **PreCompact**: logs context compaction events
- All hooks use proper SDK type annotations

## Cost Budgeting

- `max_budget_usd` parameter on `ValorAgent`
- Default: $5.00 (configurable via `SDK_MAX_BUDGET_USD` env var)
- Hard limit alongside existing `_COST_WARN_THRESHOLD` soft warning

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
- `agent/hooks/subagent_stop.py` (new)
- `agent/hooks/pre_compact.py` (new)
- `agent/sdk_client.py` (modified)
- `agent/health_check.py` (modified)
- `agent/steering.py` (modified)
- `pyproject.toml` (modified)
