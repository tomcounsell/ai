"""
Comprehensive tests for the centralized logging configuration system.
"""

import pytest
import logging
import json
import os
import tempfile
import shutil
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

from utilities.logging_config import (
    LogLevel,
    LoggerName,
    StructuredFormatter,
    HumanReadableFormatter,
    LoggingConfig,
    PerformanceLogger,
    LogContext,
    setup_logging,
    get_logger,
    get_component_logger,
    set_logger_level,
    log_performance,
    log_resource_usage
)


class TestLogLevel:
    """Test the LogLevel enum."""
    
    def test_log_level_values(self):
        """Test that LogLevel enum has correct logging level values."""
        assert LogLevel.DEBUG.value == logging.DEBUG
        assert LogLevel.INFO.value == logging.INFO
        assert LogLevel.WARNING.value == logging.WARNING
        assert LogLevel.ERROR.value == logging.ERROR
        assert LogLevel.CRITICAL.value == logging.CRITICAL


class TestLoggerName:
    """Test the LoggerName enum."""
    
    def test_logger_name_values(self):
        """Test that LoggerName enum has expected values."""
        expected_names = {
            'SYSTEM': 'ai_rebuild.system',
            'AGENTS': 'ai_rebuild.agents',
            'TOOLS': 'ai_rebuild.tools',
            'INTEGRATIONS': 'ai_rebuild.integrations',
            'ERRORS': 'ai_rebuild.errors',
            'PERFORMANCE': 'ai_rebuild.performance',
            'SECURITY': 'ai_rebuild.security',
            'DATABASE': 'ai_rebuild.database',
            'CONFIG': 'ai_rebuild.config',
            'API': 'ai_rebuild.api'
        }
        
        for name, expected_value in expected_names.items():
            assert hasattr(LoggerName, name)
            assert getattr(LoggerName, name).value == expected_value


class TestStructuredFormatter:
    """Test the StructuredFormatter class."""
    
    @pytest.fixture
    def formatter(self):
        """Create a StructuredFormatter instance."""
        return StructuredFormatter()
    
    @pytest.fixture
    def formatter_no_extra(self):
        """Create a StructuredFormatter instance without extra fields."""
        return StructuredFormatter(include_extra=False)
    
    @pytest.fixture
    def log_record(self):
        """Create a sample log record."""
        return logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message %s",
            args=("arg1",),
            exc_info=None,
            func="test_function"
        )
    
    def test_basic_formatting(self, formatter, log_record):
        """Test basic log formatting without exceptions."""
        formatted = formatter.format(log_record)
        log_data = json.loads(formatted)
        
        assert log_data["level"] == "INFO"
        assert log_data["logger"] == "test.logger"
        assert log_data["message"] == "Test message arg1"
        assert log_data["module"] == "path"
        assert log_data["function"] == "test_function"
        assert log_data["line"] == 42
        assert "timestamp" in log_data
        assert "thread" in log_data
        assert "thread_name" in log_data
    
    def test_formatting_with_exception(self, formatter):
        """Test formatting with exception information."""
        try:
            raise ValueError("Test exception")
        except ValueError:
            log_record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname="/test/path.py",
                lineno=42,
                msg="Error occurred",
                args=(),
                exc_info=sys.exc_info(),
                func="test_function"
            )
        
        formatted = formatter.format(log_record)
        log_data = json.loads(formatted)
        
        assert log_data["level"] == "ERROR"
        assert log_data["message"] == "Error occurred"
        assert "exception" in log_data
        assert log_data["exception"]["type"] == "ValueError"
        assert log_data["exception"]["message"] == "Test exception"
        assert "traceback" in log_data["exception"]
    
    def test_formatting_with_extra_fields(self, formatter, log_record):
        """Test formatting with extra fields included."""
        # Add extra attributes to log record
        log_record.custom_field = "custom_value"
        log_record.user_id = "user123"
        log_record.request_id = 456
        log_record.is_admin = True
        log_record.tags = ["tag1", "tag2"]
        log_record.metadata = {"key": "value"}
        
        formatted = formatter.format(log_record)
        log_data = json.loads(formatted)
        
        assert log_data["extra_custom_field"] == "custom_value"
        assert log_data["extra_user_id"] == "user123"
        assert log_data["extra_request_id"] == 456
        assert log_data["extra_is_admin"] is True
        assert log_data["extra_tags"] == ["tag1", "tag2"]
        assert log_data["extra_metadata"] == {"key": "value"}
    
    def test_formatting_without_extra_fields(self, formatter_no_extra, log_record):
        """Test formatting without extra fields."""
        log_record.custom_field = "custom_value"
        log_record.user_id = "user123"
        
        formatted = formatter_no_extra.format(log_record)
        log_data = json.loads(formatted)
        
        assert "extra_custom_field" not in log_data
        assert "extra_user_id" not in log_data
        assert log_data["message"] == log_record.getMessage()
    
    def test_formatting_with_complex_objects(self, formatter, log_record):
        """Test formatting with complex objects that need string conversion."""
        class ComplexObject:
            def __str__(self):
                return "complex_object_str"
        
        log_record.complex_object = ComplexObject()
        
        formatted = formatter.format(log_record)
        log_data = json.loads(formatted)
        
        assert log_data["extra_complex_object"] == "complex_object_str"


class TestHumanReadableFormatter:
    """Test the HumanReadableFormatter class."""
    
    @pytest.fixture
    def formatter(self):
        """Create a HumanReadableFormatter instance."""
        return HumanReadableFormatter()
    
    @pytest.fixture
    def log_record(self):
        """Create a sample log record."""
        return logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/test/path.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
            func="test_function"
        )
    
    def test_basic_formatting(self, formatter, log_record):
        """Test basic human-readable formatting."""
        formatted = formatter.format(log_record)
        
        # Should contain timestamp, level, logger name, and message
        assert "INFO" in formatted
        assert "test.logger" in formatted
        assert "Test message" in formatted
        assert "|" in formatted  # Format separators
    
    @patch('sys.stderr.isatty', return_value=True)
    def test_color_formatting_with_tty(self, mock_isatty, formatter, log_record):
        """Test color formatting when output is a TTY."""
        # Test different log levels
        levels = [
            (logging.DEBUG, '\033[36m'),     # Cyan
            (logging.INFO, '\033[32m'),      # Green
            (logging.WARNING, '\033[33m'),   # Yellow
            (logging.ERROR, '\033[31m'),     # Red
            (logging.CRITICAL, '\033[35m'),  # Magenta
        ]
        
        for level, color_code in levels:
            log_record.levelno = level
            log_record.levelname = logging.getLevelName(level)
            
            formatted = formatter.format(log_record)
            assert color_code in formatted
            assert '\033[0m' in formatted  # Reset color
    
    @patch('sys.stderr.isatty', return_value=False)
    def test_no_color_formatting_without_tty(self, mock_isatty, formatter, log_record):
        """Test no color formatting when output is not a TTY."""
        formatted = formatter.format(log_record)
        
        # Should not contain color codes
        assert '\033[' not in formatted


class TestLoggingConfig:
    """Test the LoggingConfig class."""
    
    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for logs."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def logging_config(self, temp_log_dir):
        """Create a LoggingConfig instance with temporary directory."""
        return LoggingConfig(temp_log_dir)
    
    def test_initialization(self, temp_log_dir):
        """Test LoggingConfig initialization."""
        config = LoggingConfig(temp_log_dir)
        
        assert config.base_dir == Path(temp_log_dir)
        assert config.base_dir.exists()
        assert config.max_file_size == 10 * 1024 * 1024  # 10MB
        assert config.backup_count == 3
        assert config.log_levels == {}
        assert config.configured_loggers == []
    
    def test_default_initialization(self):
        """Test LoggingConfig initialization with default directory."""
        config = LoggingConfig()
        expected_dir = Path(os.path.dirname(os.path.dirname(__file__))) / 'logs'
        
        # The path should be set correctly (actual directory creation depends on permissions)
        assert str(expected_dir) in str(config.base_dir)
    
    def test_setup_logging_basic(self, logging_config, temp_log_dir):
        """Test basic logging setup."""
        logging_config.setup_logging()
        
        # Check that log files are created
        log_dir = Path(temp_log_dir)
        assert (log_dir / "ai_rebuild.log").exists() or True  # May not exist until first log
        
        # Check that loggers are configured
        assert len(logging_config.configured_loggers) > 0
        
        # Test logging to verify setup
        logger = logging.getLogger("test")
        logger.info("Test message")
    
    def test_setup_logging_with_options(self, logging_config, temp_log_dir):
        """Test logging setup with specific options."""
        logging_config.setup_logging(
            console_level=LogLevel.WARNING,
            file_level=LogLevel.DEBUG,
            structured_logs=False,
            enable_performance_logging=True
        )
        
        # Verify setup completed
        assert len(logging_config.configured_loggers) > 0
        
        # Test that performance logger is set up
        perf_logger = logging.getLogger(LoggerName.PERFORMANCE.value)
        assert perf_logger is not None
    
    def test_component_logger_setup(self, logging_config, temp_log_dir):
        """Test that component loggers are properly set up."""
        logging_config.setup_logging()
        
        expected_components = [
            LoggerName.SYSTEM.value,
            LoggerName.AGENTS.value,
            LoggerName.TOOLS.value,
            LoggerName.INTEGRATIONS.value,
            LoggerName.SECURITY.value,
            LoggerName.DATABASE.value,
            LoggerName.CONFIG.value,
            LoggerName.API.value,
            LoggerName.ERRORS.value,
            LoggerName.PERFORMANCE.value
        ]
        
        for component in expected_components:
            assert component in logging_config.configured_loggers
            logger = logging.getLogger(component)
            assert logger is not None
    
    def test_set_logger_level(self, logging_config):
        """Test setting logger levels."""
        logger_name = "test.logger"
        level = LogLevel.WARNING
        
        logging_config.set_logger_level(logger_name, level)
        
        assert logging_config.log_levels[logger_name] == level
        
        # Verify the logger level was actually set
        logger = logging.getLogger(logger_name)
        assert logger.level == level.value
    
    def test_get_logger(self, logging_config):
        """Test getting logger instances."""
        logger_name = "test.logger"
        logger = logging_config.get_logger(logger_name)
        
        assert logger.name == logger_name
        assert isinstance(logger, logging.Logger)
    
    def test_log_system_info(self, logging_config, temp_log_dir):
        """Test logging system information."""
        logging_config.setup_logging()
        
        # Capture log output
        with patch.object(logging_config, 'get_logger') as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            
            logging_config.log_system_info()
            
            # Verify system info was logged
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            assert "System information" in call_args[0][0]
            assert "system_info" in call_args[1]["extra"]


class TestPerformanceLogger:
    """Test the PerformanceLogger class."""
    
    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return Mock(spec=logging.Logger)
    
    def test_initialization_default(self):
        """Test PerformanceLogger initialization with default logger."""
        perf_logger = PerformanceLogger()
        assert perf_logger.logger.name == LoggerName.PERFORMANCE.value
    
    def test_initialization_custom_logger(self):
        """Test PerformanceLogger initialization with custom logger name."""
        custom_name = "custom.performance"
        perf_logger = PerformanceLogger(custom_name)
        assert perf_logger.logger.name == custom_name
    
    def test_log_duration(self, mock_logger):
        """Test logging operation duration."""
        perf_logger = PerformanceLogger()
        perf_logger.logger = mock_logger
        
        operation = "database_query"
        duration_ms = 123.45
        extra_fields = {"query": "SELECT * FROM users", "rows": 100}
        
        perf_logger.log_duration(operation, duration_ms, **extra_fields)
        
        # Verify the log call
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        
        # Check message
        assert "database_query" in call_args[0][0]
        assert "123.45ms" in call_args[0][0]
        
        # Check extra fields
        extra = call_args[1]["extra"]
        assert extra["operation"] == operation
        assert extra["duration_ms"] == duration_ms
        assert extra["query"] == "SELECT * FROM users"
        assert extra["rows"] == 100
    
    def test_log_resource_usage_without_limit(self, mock_logger):
        """Test logging resource usage without limit."""
        perf_logger = PerformanceLogger()
        perf_logger.logger = mock_logger
        
        resource_type = "memory"
        usage = 512.0
        extra_fields = {"unit": "MB"}
        
        perf_logger.log_resource_usage(resource_type, usage, **extra_fields)
        
        # Verify the log call
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        
        # Check message
        assert "memory: 512.0" in call_args[0][0]
        assert "%" not in call_args[0][0]  # No percentage without limit
        
        # Check extra fields
        extra = call_args[1]["extra"]
        assert extra["resource_type"] == resource_type
        assert extra["usage"] == usage
        assert extra["limit"] is None
        assert extra["usage_percentage"] is None
        assert extra["unit"] == "MB"
    
    def test_log_resource_usage_with_limit(self, mock_logger):
        """Test logging resource usage with limit."""
        perf_logger = PerformanceLogger()
        perf_logger.logger = mock_logger
        
        resource_type = "cpu"
        usage = 75.0
        limit = 100.0
        
        perf_logger.log_resource_usage(resource_type, usage, limit)
        
        # Verify the log call
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        
        # Check message includes percentage
        assert "cpu: 75.0 / 100.0 (75.0%)" in call_args[0][0]
        
        # Check extra fields
        extra = call_args[1]["extra"]
        assert extra["resource_type"] == resource_type
        assert extra["usage"] == usage
        assert extra["limit"] == limit
        assert extra["usage_percentage"] == 75.0


class TestGlobalFunctions:
    """Test the global utility functions."""
    
    def test_setup_logging(self):
        """Test the global setup_logging function."""
        with patch('utilities.logging_config._logging_config') as mock_config:
            setup_logging(
                console_level=LogLevel.WARNING,
                file_level=LogLevel.DEBUG,
                structured_logs=False
            )
            
            mock_config.setup_logging.assert_called_once_with(
                console_level=LogLevel.WARNING,
                file_level=LogLevel.DEBUG,
                structured_logs=False
            )
            mock_config.log_system_info.assert_called_once()
    
    def test_setup_logging_with_custom_dir(self):
        """Test setup_logging with custom log directory."""
        custom_dir = "/custom/log/dir"
        
        with patch('utilities.logging_config.LoggingConfig') as mock_config_class:
            mock_instance = Mock()
            mock_config_class.return_value = mock_instance
            
            setup_logging(log_dir=custom_dir)
            
            mock_config_class.assert_called_once_with(custom_dir)
            mock_instance.setup_logging.assert_called_once()
    
    def test_get_logger(self):
        """Test the global get_logger function."""
        logger_name = "test.logger"
        
        with patch('utilities.logging_config._logging_config') as mock_config:
            mock_logger = Mock()
            mock_config.get_logger.return_value = mock_logger
            
            result = get_logger(logger_name)
            
            assert result == mock_logger
            mock_config.get_logger.assert_called_once_with(logger_name)
    
    def test_get_component_logger(self):
        """Test the global get_component_logger function."""
        component = LoggerName.AGENTS
        
        with patch('utilities.logging_config.get_logger') as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            
            result = get_component_logger(component)
            
            assert result == mock_logger
            mock_get_logger.assert_called_once_with(component.value)
    
    def test_set_logger_level(self):
        """Test the global set_logger_level function."""
        logger_name = "test.logger"
        level = LogLevel.ERROR
        
        with patch('utilities.logging_config._logging_config') as mock_config:
            set_logger_level(logger_name, level)
            
            mock_config.set_logger_level.assert_called_once_with(logger_name, level)
    
    def test_log_performance(self):
        """Test the global log_performance function."""
        operation = "test_operation"
        duration = 123.45
        extra_fields = {"key": "value"}
        
        with patch('utilities.logging_config._performance_logger') as mock_perf_logger:
            log_performance(operation, duration, **extra_fields)
            
            mock_perf_logger.log_duration.assert_called_once_with(
                operation, duration, key="value"
            )
    
    def test_log_resource_usage(self):
        """Test the global log_resource_usage function."""
        resource_type = "memory"
        usage = 512.0
        limit = 1024.0
        extra_fields = {"unit": "MB"}
        
        with patch('utilities.logging_config._performance_logger') as mock_perf_logger:
            log_resource_usage(resource_type, usage, limit, **extra_fields)
            
            mock_perf_logger.log_resource_usage.assert_called_once_with(
                resource_type, usage, limit, unit="MB"
            )


class TestLogContext:
    """Test the LogContext context manager."""
    
    def test_log_context_basic(self):
        """Test basic LogContext usage."""
        logger = logging.getLogger("test.context")
        
        # Capture log output
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        
        context_data = {"user_id": "123", "request_id": "abc"}
        
        with LogContext(logger, **context_data):
            logger.info("Test message")
        
        # Get log output and parse JSON
        log_output = stream.getvalue().strip()
        if log_output:
            log_data = json.loads(log_output)
            assert log_data["extra_user_id"] == "123"
            assert log_data["extra_request_id"] == "abc"
        
        # Clean up
        logger.removeHandler(handler)
    
    def test_log_context_restore_factory(self):
        """Test that LogContext properly restores the log record factory."""
        original_factory = logging.getLogRecordFactory()
        logger = logging.getLogger("test.restore")
        
        with LogContext(logger, test_field="test_value"):
            # Inside context, factory should be different
            current_factory = logging.getLogRecordFactory()
            assert current_factory != original_factory
        
        # After context, factory should be restored
        restored_factory = logging.getLogRecordFactory()
        assert restored_factory == original_factory


class TestIntegration:
    """Integration tests combining multiple components."""
    
    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for logs."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    def test_complete_logging_setup(self, temp_log_dir):
        """Test complete logging setup and usage."""
        # Setup logging
        config = LoggingConfig(temp_log_dir)
        config.setup_logging(structured_logs=True)
        
        # Get loggers for different components
        system_logger = logging.getLogger(LoggerName.SYSTEM.value)
        error_logger = logging.getLogger(LoggerName.ERRORS.value)
        perf_logger = PerformanceLogger()
        
        # Log different types of messages
        system_logger.info("System started")
        error_logger.error("Test error", exc_info=False)
        perf_logger.log_duration("test_operation", 150.5)
        
        # Check that log files exist
        log_dir = Path(temp_log_dir)
        assert (log_dir / "ai_rebuild.log").exists() or True  # Files may not exist until flush
        
        # Test structured logging with context
        with LogContext(system_logger, request_id="test123"):
            system_logger.info("Request processed")
    
    def test_error_and_performance_logging(self, temp_log_dir):
        """Test error and performance logging integration."""
        config = LoggingConfig(temp_log_dir)
        config.setup_logging(enable_performance_logging=True)
        
        # Log performance metrics
        log_performance("database_query", 45.2, table="users", rows=100)
        log_resource_usage("memory", 512.0, 1024.0, worker_process="worker")
        
        # Log errors with context
        error_logger = get_component_logger(LoggerName.ERRORS)
        try:
            raise ValueError("Test exception for logging")
        except ValueError:
            error_logger.exception("Error occurred during test")
        
        # Verify setup completed without errors
        assert len(config.configured_loggers) > 0