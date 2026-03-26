# Agent Definition Fallback

Graceful degradation when agent definition markdown files (`.claude/agents/*.md`) are missing from disk.

## Problem

When an agent definition file is absent, `_parse_agent_markdown()` in `agent/agent_definitions.py` would crash with an unhandled `FileNotFoundError`, killing the entire session. The user receives only a generic error message.

This happens during deployment windows when code is updated but agent files have not yet synced, or on new machines where `.claude/agents/` is incomplete.

## Behavior

- **Missing file detection**: `_parse_agent_markdown()` checks `path.exists()` before reading. If the file is missing, it logs a warning and returns a fallback dict with a minimal prompt instead of raising.
- **Session continues**: `get_agent_definitions()` returns a complete dict even when some or all agent files are missing. The agent operates with degraded prompts rather than crashing.
- **Bridge startup validation**: `validate_agent_files()` is called during bridge initialization to surface missing files early via log warnings, giving operators a chance to fix the issue before users hit it.

## Fallback Prompt

When an agent file is missing, the fallback prompt is:

> Agent definition file {name}.md is not available. Operate with your best judgment.

The agent's description is set to `"Fallback for missing {name}.md"`.

## Key Files

| File | Role |
|------|------|
| `agent/agent_definitions.py` | Fallback logic in `_parse_agent_markdown()`, `validate_agent_files()` |
| `bridge/telegram_bridge.py` | Calls `validate_agent_files()` at startup |
| `tests/unit/test_agent_definitions.py` | Unit tests covering normal load, missing files, and validation |

## Prior Art

The `_load_dev_session_prompt()` function in the same module already implemented this pattern with an `.exists()` check and hardcoded fallback prompt. This change extends the pattern to all agent definitions.

## Related

- Plan: `docs/plans/sdk_graceful_agent_fallback.md`
- Issue: [#539](https://github.com/tomcounsell/ai/issues/539)
