"""
Utilities package for AI Rebuild.

This package contains utility modules including database management,
migrations, error handling, logging configuration and other system utilities.
"""

# Import core modules that don't have external dependencies
from . import exceptions
from . import logging_config

# Conditionally import modules that have external dependencies
__all__ = [
    'exceptions',
    'logging_config'
]

try:
    from .database import DatabaseManager, db_manager
    from .migrations import MigrationManager, get_migration_manager
    __all__.extend([
        'DatabaseManager',
        'db_manager', 
        'MigrationManager',
        'get_migration_manager'
    ])
except ImportError:
    # Optional dependencies not available
    pass