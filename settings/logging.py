"""
Logging configuration settings.
"""

import logging
import os

from settings.env import LOCAL, PRODUCTION, STAGE

# Default log level based on environment
if PRODUCTION:
    DEFAULT_LOG_LEVEL = logging.INFO
elif STAGE:
    DEFAULT_LOG_LEVEL = logging.DEBUG
else:
    DEFAULT_LOG_LEVEL = logging.INFO

# Configure basic logging
logging.basicConfig(level=DEFAULT_LOG_LEVEL)
logger = logging.getLogger(__name__)

# Django's logging configuration
LOGGING_CONFIG = None  # Disable Django's logging config to use our own
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "file": {
            "level": "DEBUG",
            "class": "logging.FileHandler",
            "filename": os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "debug.log"
            ),
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "propagate": True,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.utils.autoreload": {
            "level": "INFO",
        },
    },
    "root": {
        "handlers": ["console", "file"] if LOCAL else ["console"],
        "level": "INFO",
    },
}

if PRODUCTION:
    # In production, configure more streamlined logging
    LOGGING["formatters"]["json"] = {
        "()": "json_log_formatter.JSONFormatter",
    }
    LOGGING["handlers"]["console"] = {
        "level": "INFO",
        "class": "logging.StreamHandler",
        "formatter": "simple",
    }
    # Don't log to file in production (use external logging service instead)
    LOGGING["root"] = {
        "handlers": ["console"],
        "level": "INFO",
    }
