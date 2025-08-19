"""
Django settings for the project.
Settings are organized into modular files for better organization.
"""

import os

# Explicit imports - will be imported as star imports below
from settings.env import BASE_DIR, LOCAL, PRODUCTION, SITE_ROOT, STAGE  # noqa: F401

# Load settings in order of increasing specificity
# This allows settings in later files to override earlier ones
DJANGO_SETTINGS_MODULE = "settings"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

from settings.base import *  # noqa: F403
from settings.database import *  # noqa: F403

# 1. First, environment - Required first to detect environment
from settings.env import *  # noqa: F403

# 2. Core settings modules
from settings.logging import *  # noqa: F403

# 3. Third-party integrations
from settings.third_party import *  # noqa: F403

# 3. Scheduler settings (if needed)
try:
    from settings.scheduler.celery import *  # noqa: F403
except ImportError:
    pass  # Celery not required

# 4. Environment-specific settings
# Production settings
if PRODUCTION:
    from settings.production import *  # noqa: F403

# Local development settings (override everything)
if LOCAL:
    try:
        from settings.local import *  # noqa: F403
    except ImportError:
        pass  # Local settings are optional
