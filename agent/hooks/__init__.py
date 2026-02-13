"""Hooks package for Claude Agent SDK integration.

Provides build_hooks_config() which returns the hooks dict
ready to pass into ClaudeAgentOptions.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import HookMatcher

from agent.hooks.post_tool_use import post_tool_use_hook
from agent.hooks.pre_compact import pre_compact_hook
from agent.hooks.pre_tool_use import pre_tool_use_hook
from agent.hooks.stop import stop_hook
from agent.hooks.subagent_stop import subagent_stop_hook


def build_hooks_config() -> dict[str, Any]:
    """Build the hooks configuration dict for ClaudeAgentOptions.

    Returns a dict mapping hook type names to lists of HookMatcher
    instances, each containing the hook functions to run.

    Hook types:
        PreToolUse: Fires before each tool call. Used to block sensitive writes.
        PostToolUse: Fires after each tool call. Runs watchdog health check.
        Stop: Fires when the main agent session ends.
        SubagentStop: Fires when a subagent finishes.
        PreCompact: Fires before context compaction.
    """
    return {
        "PreToolUse": [HookMatcher(matcher="", hooks=[pre_tool_use_hook])],
        "PostToolUse": [HookMatcher(matcher="", hooks=[post_tool_use_hook])],
        "Stop": [HookMatcher(matcher="", hooks=[stop_hook])],
        "SubagentStop": [HookMatcher(matcher="", hooks=[subagent_stop_hook])],
        "PreCompact": [HookMatcher(matcher="", hooks=[pre_compact_hook])],
    }


__all__ = ["build_hooks_config"]
