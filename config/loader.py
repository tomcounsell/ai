"""
Configuration Loader with Validation

This module provides utilities for loading and validating configuration,
environment variables, and runtime settings.
"""

import logging
from contextlib import contextmanager
from typing import Any

from .settings import Settings
from .settings import settings as global_settings

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration loading or validation fails."""

    pass


class ConfigLoader:
    """Configuration loader with validation and caching."""

    def __init__(self, settings_instance: Settings | None = None):
        """Initialize the configuration loader.

        Args:
            settings_instance: Optional settings instance to use. Defaults to global settings.
        """
        self.settings = settings_instance or global_settings

    def validate_api_keys(self) -> dict[str, bool]:
        """Validate that required API keys are configured.

        Returns:
            Dictionary mapping service names to validation status.
        """
        validation_results = {}

        # Check API keys
        api_config = self.settings.get_api_config()

        services = ["claude", "openai", "perplexity", "notion", "telegram"]
        for service in services:
            validation_results[service] = service in api_config

        return validation_results

    def validate_directories(self) -> dict[str, bool]:
        """Validate that required directories exist and are accessible.

        Returns:
            Dictionary mapping directory names to validation status.
        """
        validation_results = {}

        directories = {
            "data": self.settings.workspace.data_dir,
            "temp": self.settings.workspace.temp_dir,
            "logs": (
                self.settings.logging.file_path.parent if self.settings.logging.file_path else None
            ),
            "config": self.settings.paths.config_dir,
            "database": self.settings.database.path.parent,
        }

        for name, path in directories.items():
            if path is None:
                validation_results[name] = False
                continue

            try:
                # Try to create directory if it doesn't exist
                path.mkdir(parents=True, exist_ok=True)
                # Test write access
                test_file = path / ".write_test"
                test_file.write_text("test")
                test_file.unlink()
                validation_results[name] = True
            except Exception as e:
                logger.warning(f"Directory validation failed for {name} ({path}): {e}")
                validation_results[name] = False

        return validation_results

    def get_configuration_summary(self) -> dict[str, Any]:
        """Get a summary of current configuration status.

        Returns:
            Dictionary with configuration summary information.
        """
        return {
            "environment": self.settings.environment,
            "debug": self.settings.debug,
            "api_keys": self.validate_api_keys(),
            "directories": self.validate_directories(),
            "database_path": str(self.settings.database.path),
            "log_level": self.settings.logging.level.value,
            "server": {
                "host": self.settings.server.host,
                "port": self.settings.server.port,
                "workers": self.settings.server.workers,
            },
            "performance": {
                "max_workers": self.settings.performance.max_workers,
                "memory_limit": self.settings.performance.memory_limit,
                "timeout": self.settings.performance.timeout,
            },
        }

    @contextmanager
    def temporary_settings(self, **overrides):
        """Context manager for temporarily overriding settings.

        Args:
            **overrides: Settings to override temporarily.

        Example:
            with loader.temporary_settings(debug=True, log_level='DEBUG'):
                # Settings are temporarily overridden
                pass
            # Settings are restored
        """
        # Store original values
        original_values = {}

        try:
            # Apply overrides
            for key, value in overrides.items():
                if hasattr(self.settings, key):
                    original_values[key] = getattr(self.settings, key)
                    setattr(self.settings, key, value)
                else:
                    logger.warning(f"Unknown setting: {key}")

            yield self.settings

        finally:
            # Restore original values
            for key, value in original_values.items():
                setattr(self.settings, key, value)


# Global configuration loader instance
config_loader = ConfigLoader()


def validate_configuration() -> bool:
    """Validate the complete configuration setup.

    Returns:
        True if configuration is valid, False otherwise.
    """
    try:
        # Validate settings
        global_settings.create_directories()
        global_settings.setup_logging()

        # Check API keys
        api_validation = config_loader.validate_api_keys()
        if not any(api_validation.values()):
            logger.warning("No API keys configured")

        # Check directories
        dir_validation = config_loader.validate_directories()
        if not all(dir_validation.values()):
            failed_dirs = [name for name, status in dir_validation.items() if not status]
            logger.warning(f"Directory validation failed for: {', '.join(failed_dirs)}")

        logger.info("Configuration validation completed successfully")
        return True

    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        return False
