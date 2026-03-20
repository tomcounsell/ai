"""
Path constants derived from project root.

All paths are derived from Path(__file__) -- no hardcoded usernames.
Import these instead of constructing paths manually.

Usage:
    from config.paths import PROJECT_ROOT, DATA_DIR, CONFIG_DIR, VALOR_DIR
"""

from pathlib import Path

# Project root: parent of config/ directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Standard directories
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_DIR = PROJECT_ROOT / "config"

# Home-relative paths (for user-specific locations)
HOME_DIR = Path.home()
SRC_DIR = HOME_DIR / "src"

# Canonical credentials directory (Google OAuth, DM whitelist, calendar config)
VALOR_DIR = HOME_DIR / "Desktop" / "Valor"
