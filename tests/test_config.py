"""
Tests for configuration management system.

This module contains comprehensive tests for settings, configuration loading,
validation, and workspace configuration management.
"""

import json
import tempfile
import pytest
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch, MagicMock

from pydantic import ValidationError

from config import (
    Settings,
    DatabaseSettings,
    APISettings,
    TelegramSettings,
    ServerSettings,
    SecuritySettings,
    LoggingSettings,
    WorkspaceSettings,
    PerformanceSettings,
    LogLevel,
    ConfigLoader,
    WorkspaceConfig,
    ConfigurationError,
    validate_configuration,
)


class TestDatabaseSettings:
    """Test database configuration settings."""
    
    def test_default_values(self):
        """Test database settings with default values."""
        db_settings = DatabaseSettings()
        
        assert db_settings.path == Path("data/ai_rebuild.db")
        assert db_settings.echo is False
        assert db_settings.pool_size == 20
    
    def test_custom_values(self):
        """Test database settings with custom values."""
        db_settings = DatabaseSettings(
            path=Path("/custom/path/db.sqlite"),
            echo=True,
            pool_size=10
        )
        
        assert db_settings.path == Path("/custom/path/db.sqlite")
        assert db_settings.echo is True
        assert db_settings.pool_size == 10
    
    def test_validation_pool_size(self):
        """Test pool size validation."""
        with pytest.raises(ValidationError):
            DatabaseSettings(pool_size=0)
        
        with pytest.raises(ValidationError):
            DatabaseSettings(pool_size=101)


class TestAPISettings:
    """Test API configuration settings."""
    
    def test_default_values(self):
        """Test API settings with default values."""
        api_settings = APISettings()
        
        assert api_settings.claude_api_key is None
        assert api_settings.openai_api_key is None
        assert api_settings.perplexity_api_key is None
        assert api_settings.notion_api_key is None
    
    def test_api_key_validation(self):
        """Test API key validation."""
        # Valid API key
        api_settings = APISettings(claude_api_key="sk-1234567890abcdef")
        assert api_settings.claude_api_key == "sk-1234567890abcdef"
        
        # Invalid API key (too short)
        with pytest.raises(ValidationError):
            APISettings(claude_api_key="short")
        
        # Empty string should be None
        api_settings = APISettings(claude_api_key="")
        assert api_settings.claude_api_key is None


class TestTelegramSettings:
    """Test Telegram configuration settings."""
    
    def test_default_values(self):
        """Test Telegram settings with default values."""
        tg_settings = TelegramSettings()
        
        assert tg_settings.api_id is None
        assert tg_settings.api_hash is None
        assert tg_settings.session_name == "ai_rebuild_session"
    
    def test_api_hash_validation(self):
        """Test Telegram API hash validation."""
        # Valid API hash (32 characters)
        valid_hash = "a" * 32
        tg_settings = TelegramSettings(api_hash=valid_hash)
        assert tg_settings.api_hash == valid_hash
        
        # Invalid API hash (wrong length)
        with pytest.raises(ValidationError):
            TelegramSettings(api_hash="short_hash")
    
    def test_api_id_validation(self):
        """Test Telegram API ID validation."""
        # Valid API ID
        tg_settings = TelegramSettings(api_id=123456)
        assert tg_settings.api_id == 123456
        
        # Invalid API ID (negative)
        with pytest.raises(ValidationError):
            TelegramSettings(api_id=-1)


class TestServerSettings:
    """Test server configuration settings."""
    
    def test_default_values(self):
        """Test server settings with default values."""
        server_settings = ServerSettings()
        
        assert server_settings.host == "127.0.0.1"
        assert server_settings.port == 8000
        assert server_settings.reload is False
        assert server_settings.workers == 1
    
    def test_port_validation(self):
        """Test port validation."""
        # Valid ports
        ServerSettings(port=8080)
        ServerSettings(port=65535)
        
        # Invalid ports
        with pytest.raises(ValidationError):
            ServerSettings(port=999)
        
        with pytest.raises(ValidationError):
            ServerSettings(port=65536)


class TestLoggingSettings:
    """Test logging configuration settings."""
    
    def test_default_values(self):
        """Test logging settings with default values."""
        log_settings = LoggingSettings()
        
        assert log_settings.level == LogLevel.INFO
        assert log_settings.file_path == Path("logs/ai_rebuild.log")
        assert log_settings.max_file_size == 10 * 1024 * 1024
        assert log_settings.backup_count == 5
    
    def test_log_level_enum(self):
        """Test log level enumeration."""
        for level in LogLevel:
            log_settings = LoggingSettings(level=level)
            assert log_settings.level == level


class TestSettings:
    """Test main Settings class."""
    
    def test_default_settings(self):
        """Test settings with default values."""
        settings = Settings()
        
        assert settings.environment == "development"
        assert settings.debug is False
        assert isinstance(settings.database, DatabaseSettings)
        assert isinstance(settings.api, APISettings)
        assert isinstance(settings.telegram, TelegramSettings)
        assert isinstance(settings.server, ServerSettings)
        assert isinstance(settings.security, SecuritySettings)
        assert isinstance(settings.logging, LoggingSettings)
        assert isinstance(settings.workspace, WorkspaceSettings)
        assert isinstance(settings.performance, PerformanceSettings)
    
    def test_environment_validation(self):
        """Test environment validation."""
        # Valid environments
        for env in ['development', 'staging', 'production', 'testing']:
            settings = Settings(environment=env)
            assert settings.environment == env
        
        # Invalid environment
        with pytest.raises(ValidationError):
            Settings(environment="invalid_env")
    
    def test_database_url_generation(self):
        """Test database URL generation."""
        settings = Settings()
        url = settings.get_database_url()
        assert url.startswith("sqlite:///")
        assert "ai_rebuild.db" in url
    
    def test_environment_checks(self):
        """Test environment check methods."""
        dev_settings = Settings(environment="development")
        assert dev_settings.is_development() is True
        assert dev_settings.is_production() is False
        
        prod_settings = Settings(environment="production")
        assert prod_settings.is_development() is False
        assert prod_settings.is_production() is True
    
    def test_api_config_generation(self):
        """Test API configuration generation."""
        settings = Settings()
        
        # No API keys configured
        config = settings.get_api_config()
        assert config == {}
        
        # With API keys
        settings.api.claude_api_key = "claude-key"
        settings.api.openai_api_key = "openai-key"
        settings.telegram.api_id = 123456
        settings.telegram.api_hash = "a" * 32
        
        config = settings.get_api_config()
        
        assert 'claude' in config
        assert config['claude']['api_key'] == "claude-key"
        assert 'openai' in config
        assert config['openai']['api_key'] == "openai-key"
        assert 'telegram' in config
        assert config['telegram']['api_id'] == 123456


class TestWorkspaceConfig:
    """Test workspace configuration model."""
    
    @pytest.fixture
    def sample_workspace_config(self) -> Dict[str, Any]:
        """Sample workspace configuration data."""
        return {
            "workspace": {
                "name": "Test Workspace",
                "version": "1.0.0",
                "description": "Test workspace",
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z"
            },
            "agents": {
                "default_model": "claude-3-sonnet",
                "max_concurrent": 5,
                "timeout": 300,
                "retry_attempts": 3,
                "available_agents": [
                    {
                        "id": "test-agent",
                        "name": "Test Agent",
                        "model": "claude-3-sonnet",
                        "description": "Test agent",
                        "capabilities": ["test"],
                        "max_tokens": 4096,
                        "temperature": 0.7,
                        "enabled": True
                    }
                ]
            },
            "tools": {
                "enabled": True,
                "auto_discovery": True,
                "tool_directories": ["tools/"],
                "available_tools": [
                    {
                        "id": "test-tool",
                        "name": "Test Tool",
                        "type": "builtin",
                        "description": "Test tool",
                        "capabilities": ["test"],
                        "enabled": True,
                        "config": {}
                    }
                ]
            },
            "workflows": {
                "enabled": True,
                "auto_save": True,
                "max_concurrent": 3,
                "default_timeout": 600,
                "templates": [
                    {
                        "id": "test-workflow",
                        "name": "Test Workflow",
                        "description": "Test workflow",
                        "steps": [
                            {
                                "id": "step1",
                                "agent": "test-agent",
                                "action": "test_action",
                                "timeout": 60
                            }
                        ],
                        "enabled": True
                    }
                ]
            },
            "integrations": {
                "telegram": {
                    "enabled": False,
                    "bot_token": None,
                    "webhook_url": None,
                    "allowed_users": [],
                    "commands": {}
                },
                "notion": {
                    "enabled": False,
                    "workspace_id": None,
                    "default_database": None,
                    "sync_settings": {}
                },
                "mcp": {
                    "enabled": True,
                    "server_discovery": True,
                    "auto_connect": False,
                    "servers": []
                }
            },
            "security": {
                "sandbox_mode": True,
                "allowed_domains": ["localhost"],
                "blocked_commands": ["rm -rf"],
                "file_restrictions": {}
            },
            "monitoring": {
                "enabled": True,
                "log_level": "INFO",
                "metrics": {},
                "alerts": {}
            },
            "backup": {
                "enabled": True,
                "auto_backup": True,
                "backup_interval": 86400,
                "retention_count": 7,
                "backup_locations": ["data/backups/"],
                "include_files": ["config/"],
                "exclude_patterns": ["*.tmp"]
            }
        }
    
    def test_valid_workspace_config(self, sample_workspace_config):
        """Test workspace configuration validation with valid data."""
        config = WorkspaceConfig(**sample_workspace_config)
        
        assert config.workspace.name == "Test Workspace"
        assert len(config.agents.available_agents) == 1
        assert len(config.tools.available_tools) == 1
        assert len(config.workflows.templates) == 1
    
    def test_invalid_workspace_config(self):
        """Test workspace configuration validation with invalid data."""
        with pytest.raises(ValidationError):
            WorkspaceConfig(workspace={})  # Missing required fields


class TestConfigLoader:
    """Test configuration loader."""
    
    @pytest.fixture
    def temp_config_file(self, sample_workspace_config):
        """Create a temporary workspace configuration file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(sample_workspace_config, f)
            return Path(f.name)
    
    @pytest.fixture
    def config_loader_with_temp_file(self, temp_config_file):
        """Create a config loader with temporary settings."""
        settings = Settings()
        settings.workspace.config_path = temp_config_file
        return ConfigLoader(settings)
    
    @pytest.fixture
    def sample_workspace_config(self):
        """Sample workspace configuration data."""
        return {
            "workspace": {
                "name": "Test Workspace",
                "version": "1.0.0",
                "description": "Test workspace",
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z"
            },
            "agents": {
                "default_model": "claude-3-sonnet",
                "max_concurrent": 5,
                "timeout": 300,
                "retry_attempts": 3,
                "available_agents": []
            },
            "tools": {
                "enabled": True,
                "auto_discovery": True,
                "tool_directories": [],
                "available_tools": []
            },
            "workflows": {
                "enabled": True,
                "auto_save": True,
                "max_concurrent": 3,
                "default_timeout": 600,
                "templates": []
            },
            "integrations": {
                "telegram": {
                    "enabled": False,
                    "bot_token": None,
                    "webhook_url": None,
                    "allowed_users": [],
                    "commands": {}
                },
                "notion": {
                    "enabled": False,
                    "workspace_id": None,
                    "default_database": None,
                    "sync_settings": {}
                },
                "mcp": {
                    "enabled": True,
                    "server_discovery": True,
                    "auto_connect": False,
                    "servers": []
                }
            },
            "security": {
                "sandbox_mode": True,
                "allowed_domains": [],
                "blocked_commands": [],
                "file_restrictions": {}
            },
            "monitoring": {
                "enabled": True,
                "log_level": "INFO",
                "metrics": {},
                "alerts": {}
            },
            "backup": {
                "enabled": True,
                "auto_backup": True,
                "backup_interval": 86400,
                "retention_count": 7,
                "backup_locations": [],
                "include_files": [],
                "exclude_patterns": []
            }
        }
    
    def test_load_workspace_config_success(self, config_loader_with_temp_file):
        """Test successful workspace configuration loading."""
        config = config_loader_with_temp_file.load_workspace_config()
        
        assert isinstance(config, WorkspaceConfig)
        assert config.workspace.name == "Test Workspace"
    
    def test_load_workspace_config_missing_file(self):
        """Test loading workspace config with missing file."""
        settings = Settings()
        settings.workspace.config_path = Path("/nonexistent/config.json")
        loader = ConfigLoader(settings)
        
        with pytest.raises(ConfigurationError):
            loader.load_workspace_config()
    
    def test_load_workspace_config_caching(self, config_loader_with_temp_file):
        """Test workspace configuration caching."""
        # Load config twice
        config1 = config_loader_with_temp_file.load_workspace_config()
        config2 = config_loader_with_temp_file.load_workspace_config()
        
        # Should be the same cached instance
        assert config1 is config2
    
    def test_save_workspace_config(self, sample_workspace_config):
        """Test saving workspace configuration."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "test_config.json"
            settings = Settings()
            settings.workspace.config_path = config_path
            loader = ConfigLoader(settings)
            
            # Save configuration
            loader.save_workspace_config(sample_workspace_config)
            
            # Verify file was created
            assert config_path.exists()
            
            # Load and verify content
            loaded_config = loader.load_workspace_config()
            assert loaded_config.workspace.name == "Test Workspace"
    
    def test_validate_api_keys(self):
        """Test API key validation."""
        settings = Settings()
        settings.api.claude_api_key = "test-key"
        loader = ConfigLoader(settings)
        
        validation = loader.validate_api_keys()
        
        assert validation['claude'] is True
        assert validation['openai'] is False
        assert validation['perplexity'] is False
        assert validation['notion'] is False
        assert validation['telegram'] is False
    
    def test_validate_directories(self):
        """Test directory validation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings()
            settings.workspace.data_dir = Path(temp_dir) / "data"
            loader = ConfigLoader(settings)
            
            validation = loader.validate_directories()
            
            # Should be True because directories are created
            assert validation['data'] is True
    
    def test_get_configuration_summary(self, config_loader_with_temp_file):
        """Test configuration summary generation."""
        summary = config_loader_with_temp_file.get_configuration_summary()
        
        assert 'environment' in summary
        assert 'api_keys' in summary
        assert 'directories' in summary
        assert 'workspace' in summary
        assert summary['workspace']['name'] == "Test Workspace"
    
    def test_temporary_settings_context(self):
        """Test temporary settings context manager."""
        settings = Settings(debug=False)
        loader = ConfigLoader(settings)
        
        assert settings.debug is False
        
        with loader.temporary_settings(debug=True):
            assert settings.debug is True
        
        assert settings.debug is False


class TestConfigurationIntegration:
    """Integration tests for the complete configuration system."""
    
    def test_validate_configuration_success(self):
        """Test successful configuration validation."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a minimal workspace config
            config_path = Path(temp_dir) / "workspace_config.json"
            config_data = {
                "workspace": {
                    "name": "Test",
                    "version": "1.0.0",
                    "description": "Test",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z"
                },
                "agents": {"default_model": "test", "max_concurrent": 1, "timeout": 60, "retry_attempts": 1, "available_agents": []},
                "tools": {"enabled": True, "auto_discovery": True, "tool_directories": [], "available_tools": []},
                "workflows": {"enabled": True, "auto_save": True, "max_concurrent": 1, "default_timeout": 60, "templates": []},
                "integrations": {
                    "telegram": {"enabled": False, "commands": {}},
                    "notion": {"enabled": False, "sync_settings": {}},
                    "mcp": {"enabled": True, "server_discovery": True, "auto_connect": False, "servers": []}
                },
                "security": {"sandbox_mode": True, "allowed_domains": [], "blocked_commands": [], "file_restrictions": {}},
                "monitoring": {"enabled": True, "log_level": "INFO", "metrics": {}, "alerts": {}},
                "backup": {"enabled": True, "auto_backup": True, "backup_interval": 86400, "retention_count": 7, "backup_locations": [], "include_files": [], "exclude_patterns": []}
            }
            
            with open(config_path, 'w') as f:
                json.dump(config_data, f)
            
            # Mock global settings to use our temp directory
            with patch('config.loader.global_settings') as mock_settings:
                mock_settings.workspace.config_path = config_path
                mock_settings.workspace.data_dir = Path(temp_dir) / "data"
                mock_settings.logging.file_path = Path(temp_dir) / "logs" / "test.log"
                mock_settings.database.path = Path(temp_dir) / "test.db"
                mock_settings.create_directories = MagicMock()
                mock_settings.setup_logging = MagicMock()
                
                result = validate_configuration()
                assert result is True


if __name__ == "__main__":
    pytest.main([__file__])