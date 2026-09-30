"""
Tools Package

This package contains tools that extend Valor's capabilities.
Each tool follows the standard defined in STANDARD.md.

Usage:
    # Create a new tool
    python tools/new_tool.py <name>

    # Validate tools
    python tools/validate.py

    # Run tests
    pytest tools/ -v
"""

try:  # ambient production-flush guard; see docs/features/redis-flush-hardening.md
    from tools.redis_flush_guard import arm

    arm()
except Exception:  # noqa: S110 -- D2b-i: nothing may break `import tools`
    pass
# This is safe on the hot path precisely because arm() is lazy (D2a): it
# imports no `redis`, it only inserts a meta-path finder (plus a single
# `stat()` self-heal check). The bare `except Exception: pass` is mandatory
# -- nothing may ever break `import tools`, since it is on the path of every
# `python -m tools.*` CLI, every hook, and every first-party import.
