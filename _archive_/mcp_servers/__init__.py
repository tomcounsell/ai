"""MCP servers exposed to Claude Code sessions.

Each server is a stdio-transport process spawned by Claude Code on
session start. Servers provide first-class tool access to internal
project capabilities (memory recall, future: knowledge index, etc.).

Registered in ``~/.claude.json`` under ``mcpServers``. The
``scripts/update/run.py`` update step verifies registration on every
run and self-heals drift.
"""
