"""PostToolUse hook: wraps the existing watchdog health check."""

from agent.health_check import watchdog_hook as post_tool_use_hook

__all__ = ["post_tool_use_hook"]
