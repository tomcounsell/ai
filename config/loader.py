"""
Configuration Loader with Validation

This module provides utilities for loading and validating configuration files,
including workspace configuration, environment variables, and runtime settings.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union, List
from contextlib import contextmanager

from pydantic import BaseModel, ValidationError

from .settings import Settings, settings as global_settings


logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration loading or validation fails."""
    pass


class WorkspaceConfig(BaseModel):
    """Pydantic model for workspace configuration validation."""
    
    class Workspace(BaseModel):
        name: str
        version: str
        description: str
        created_at: str
        updated_at: str
    
    class Agent(BaseModel):
        id: str
        name: str
        model: str
        description: str
        capabilities: List[str]
        max_tokens: int
        temperature: float
        enabled: bool
    
    class Agents(BaseModel):
        default_model: str
        max_concurrent: int
        timeout: int
        retry_attempts: int
        available_agents: List['WorkspaceConfig.Agent']
    
    class Tool(BaseModel):
        id: str
        name: str
        type: str
        description: str
        capabilities: List[str]
        enabled: bool
        config: Dict[str, Any] = {}
    
    class Tools(BaseModel):
        enabled: bool
        auto_discovery: bool
        tool_directories: List[str]
        available_tools: List['WorkspaceConfig.Tool']
    
    class WorkflowStep(BaseModel):
        id: str
        timeout: int
        agent: Optional[str] = None
        tool: Optional[str] = None
        action: str
    
    class WorkflowTemplate(BaseModel):
        id: str
        name: str
        description: str
        steps: List['WorkspaceConfig.WorkflowStep']
        enabled: bool
    
    class Workflows(BaseModel):
        enabled: bool
        auto_save: bool
        max_concurrent: int
        default_timeout: int
        templates: List['WorkspaceConfig.WorkflowTemplate']
    
    class TelegramIntegration(BaseModel):
        enabled: bool
        bot_token: Optional[str] = None
        webhook_url: Optional[str] = None
        allowed_users: List[str] = []
        commands: Dict[str, str] = {}
    
    class NotionIntegration(BaseModel):
        enabled: bool
        workspace_id: Optional[str] = None
        default_database: Optional[str] = None
        sync_settings: Dict[str, Any] = {}
    
    class MCPServer(BaseModel):
        name: str
        command: str
        args: List[str]
        env: Dict[str, str] = {}
        enabled: bool
    
    class MCPIntegration(BaseModel):
        enabled: bool
        server_discovery: bool
        auto_connect: bool
        servers: List['WorkspaceConfig.MCPServer']
    
    class Integrations(BaseModel):
        telegram: 'WorkspaceConfig.TelegramIntegration'
        notion: 'WorkspaceConfig.NotionIntegration'
        mcp: 'WorkspaceConfig.MCPIntegration'
    
    class Security(BaseModel):
        sandbox_mode: bool
        allowed_domains: List[str]
        blocked_commands: List[str]
        file_restrictions: Dict[str, Any]
    
    class Monitoring(BaseModel):
        enabled: bool
        log_level: str
        metrics: Dict[str, Any]
        alerts: Dict[str, Any]
    
    class Backup(BaseModel):
        enabled: bool
        auto_backup: bool
        backup_interval: int
        retention_count: int
        backup_locations: List[str]
        include_files: List[str]
        exclude_patterns: List[str]
    
    workspace: Workspace
    agents: Agents
    tools: Tools
    workflows: Workflows
    integrations: Integrations
    security: Security
    monitoring: Monitoring
    backup: Backup


class ConfigLoader:
    """Configuration loader with validation and caching."""
    
    def __init__(self, settings_instance: Optional[Settings] = None):
        """Initialize the configuration loader.
        
        Args:
            settings_instance: Optional settings instance to use. Defaults to global settings.
        """
        self.settings = settings_instance or global_settings
        self._workspace_config_cache: Optional[WorkspaceConfig] = None
        self._cache_timestamp: Optional[float] = None
    
    def load_workspace_config(self, force_reload: bool = False) -> WorkspaceConfig:
        """Load and validate workspace configuration.
        
        Args:
            force_reload: Force reload even if cached version exists.
            
        Returns:
            Validated workspace configuration.
            
        Raises:
            ConfigurationError: If configuration loading or validation fails.
        """
        config_path = self.settings.workspace.config_path
        
        # Check if we can use cached version
        if not force_reload and self._workspace_config_cache:
            try:
                current_mtime = config_path.stat().st_mtime
                if self._cache_timestamp and current_mtime <= self._cache_timestamp:
                    logger.debug(f"Using cached workspace config from {config_path}")
                    return self._workspace_config_cache
            except (OSError, IOError):
                # File doesn't exist or can't be accessed, continue to load
                pass
        
        logger.info(f"Loading workspace configuration from {config_path}")
        
        try:
            # Load JSON configuration
            if not config_path.exists():
                raise ConfigurationError(f"Workspace config file not found: {config_path}")
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # Validate configuration
            workspace_config = WorkspaceConfig(**config_data)
            
            # Cache the configuration
            self._workspace_config_cache = workspace_config
            self._cache_timestamp = config_path.stat().st_mtime
            
            logger.info("Workspace configuration loaded and validated successfully")
            return workspace_config
            
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in workspace config: {e}")
        except ValidationError as e:
            raise ConfigurationError(f"Workspace config validation failed: {e}")
        except Exception as e:
            raise ConfigurationError(f"Failed to load workspace config: {e}")
    
    def save_workspace_config(self, config: Union[WorkspaceConfig, Dict[str, Any]]) -> None:
        """Save workspace configuration to file.
        
        Args:
            config: Configuration to save (either WorkspaceConfig instance or dict).
            
        Raises:
            ConfigurationError: If configuration saving fails.
        """
        config_path = self.settings.workspace.config_path
        
        try:
            # Ensure directory exists
            config_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert to dict if needed
            if isinstance(config, WorkspaceConfig):
                config_data = config.model_dump()
            else:
                # Validate dict format
                WorkspaceConfig(**config)
                config_data = config
            
            # Save to file with proper formatting
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            
            # Invalidate cache
            self._workspace_config_cache = None
            self._cache_timestamp = None
            
            logger.info(f"Workspace configuration saved to {config_path}")
            
        except ValidationError as e:
            raise ConfigurationError(f"Invalid workspace configuration: {e}")
        except Exception as e:
            raise ConfigurationError(f"Failed to save workspace config: {e}")
    
    def validate_api_keys(self) -> Dict[str, bool]:
        """Validate that required API keys are configured.
        
        Returns:
            Dictionary mapping service names to validation status.
        """
        validation_results = {}
        
        # Check API keys
        api_config = self.settings.get_api_config()
        
        services = ['claude', 'openai', 'perplexity', 'notion', 'telegram']
        for service in services:
            validation_results[service] = service in api_config
        
        return validation_results
    
    def validate_directories(self) -> Dict[str, bool]:
        """Validate that required directories exist and are accessible.
        
        Returns:
            Dictionary mapping directory names to validation status.
        """
        validation_results = {}
        
        directories = {
            'data': self.settings.workspace.data_dir,
            'temp': self.settings.workspace.temp_dir,
            'logs': self.settings.logging.file_path.parent if self.settings.logging.file_path else None,
            'config': self.settings.workspace.config_path.parent,
            'database': self.settings.database.path.parent,
        }
        
        for name, path in directories.items():
            if path is None:
                validation_results[name] = False
                continue
                
            try:
                # Try to create directory if it doesn't exist
                path.mkdir(parents=True, exist_ok=True)
                # Test write access
                test_file = path / '.write_test'
                test_file.write_text('test')
                test_file.unlink()
                validation_results[name] = True
            except Exception as e:
                logger.warning(f"Directory validation failed for {name} ({path}): {e}")
                validation_results[name] = False
        
        return validation_results
    
    def get_configuration_summary(self) -> Dict[str, Any]:
        """Get a summary of current configuration status.
        
        Returns:
            Dictionary with configuration summary information.
        """
        summary = {
            'environment': self.settings.environment,
            'debug': self.settings.debug,
            'api_keys': self.validate_api_keys(),
            'directories': self.validate_directories(),
            'database_path': str(self.settings.database.path),
            'log_level': self.settings.logging.level.value,
            'server': {
                'host': self.settings.server.host,
                'port': self.settings.server.port,
                'workers': self.settings.server.workers
            },
            'performance': {
                'max_workers': self.settings.performance.max_workers,
                'memory_limit': self.settings.performance.memory_limit,
                'timeout': self.settings.performance.timeout
            }
        }
        
        # Add workspace config status
        try:
            workspace_config = self.load_workspace_config()
            summary['workspace'] = {
                'name': workspace_config.workspace.name,
                'version': workspace_config.workspace.version,
                'agents_count': len(workspace_config.agents.available_agents),
                'tools_count': len(workspace_config.tools.available_tools),
                'workflows_count': len(workspace_config.workflows.templates)
            }
        except ConfigurationError as e:
            summary['workspace'] = {'error': str(e)}
        
        return summary
    
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


def load_workspace_config(force_reload: bool = False) -> WorkspaceConfig:
    """Convenience function to load workspace configuration.
    
    Args:
        force_reload: Force reload even if cached version exists.
        
    Returns:
        Validated workspace configuration.
    """
    return config_loader.load_workspace_config(force_reload=force_reload)


def validate_configuration() -> bool:
    """Validate the complete configuration setup.
    
    Returns:
        True if configuration is valid, False otherwise.
    """
    try:
        # Validate settings
        global_settings.create_directories()
        global_settings.setup_logging()
        
        # Validate workspace config
        config_loader.load_workspace_config()
        
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


# Update WorkspaceConfig forward references
WorkspaceConfig.model_rebuild()