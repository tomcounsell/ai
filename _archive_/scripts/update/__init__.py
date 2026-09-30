"""
Update system modules.

Modular update system for Valor. Can be called from:
- /update skill (full update with all checks)
- remote-update.sh cron (minimal automated update)
- Direct Python invocation for testing
"""

from .run import UpdateConfig, run_update

__all__ = ["run_update", "UpdateConfig"]
