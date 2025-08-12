"""
Centralized Logging Configuration for AI System

This module provides structured logging configuration with rotating file handlers,
separate loggers for different system components, and log aggregation support.
"""

import logging
import logging.handlers
import sys
import os
import json
from pathlib import Path
from typing import Dict, Optional, Any, List
from datetime import datetime
import threading
from enum import Enum


class LogLevel(Enum):
    """Standard log levels."""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class LoggerName(Enum):
    """Predefined logger names for system components."""
    SYSTEM = "ai_rebuild.system"
    AGENTS = "ai_rebuild.agents"
    TOOLS = "ai_rebuild.tools"
    INTEGRATIONS = "ai_rebuild.integrations"
    ERRORS = "ai_rebuild.errors"
    PERFORMANCE = "ai_rebuild.performance"
    SECURITY = "ai_rebuild.security"
    DATABASE = "ai_rebuild.database"
    CONFIG = "ai_rebuild.config"
    API = "ai_rebuild.api"


class StructuredFormatter(logging.Formatter):
    """
    Custom formatter that outputs structured JSON logs.
    """
    
    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as structured JSON."""
        log_entry = {
            'timestamp': datetime.now().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'thread': record.thread,
            'thread_name': record.threadName,
        }
        
        # Add exception information if present
        if record.exc_info:
            log_entry['exception'] = {
                'type': record.exc_info[0].__name__ if record.exc_info[0] else None,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None,
                'traceback': self.formatException(record.exc_info) if record.exc_info else None
            }
        
        # Add any extra fields if enabled
        if self.include_extra:
            for key, value in record.__dict__.items():
                if key not in {
                    'name', 'msg', 'args', 'levelname', 'levelno', 'pathname',
                    'filename', 'module', 'exc_info', 'exc_text', 'stack_info',
                    'lineno', 'funcName', 'created', 'msecs', 'relativeCreated',
                    'thread', 'threadName', 'processName', 'process', 'getMessage'
                }:
                    if isinstance(value, (str, int, float, bool, list, dict)):
                        log_entry[f'extra_{key}'] = value
                    else:
                        log_entry[f'extra_{key}'] = str(value)
        
        return json.dumps(log_entry, default=str, ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    """
    Human-readable formatter for console output.
    """
    
    def __init__(self):
        super().__init__(
            fmt='%(asctime)s | %(levelname)8s | %(name)20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Color codes for different log levels
        self.colors = {
            'DEBUG': '\033[36m',    # Cyan
            'INFO': '\033[32m',     # Green
            'WARNING': '\033[33m',  # Yellow
            'ERROR': '\033[31m',    # Red
            'CRITICAL': '\033[35m', # Magenta
        }
        self.reset_color = '\033[0m'
    
    def format(self, record: logging.LogRecord) -> str:
        """Format with colors if output is a terminal."""
        formatted = super().format(record)
        
        # Add colors if we're outputting to a terminal
        if hasattr(sys.stderr, 'isatty') and sys.stderr.isatty():
            color = self.colors.get(record.levelname, '')
            if color:
                formatted = f"{color}{formatted}{self.reset_color}"
        
        return formatted


class LoggingConfig:
    """
    Centralized logging configuration manager.
    """
    
    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize logging configuration.
        
        Args:
            base_dir: Base directory for log files. Defaults to project root/logs
        """
        if base_dir is None:
            base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
        
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True, parents=True)
        
        # Configuration
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.backup_count = 3
        self.log_levels: Dict[str, LogLevel] = {}
        self.configured_loggers: List[str] = []
        
        # Thread safety
        self._lock = threading.Lock()
    
    def setup_logging(
        self,
        console_level: LogLevel = LogLevel.INFO,
        file_level: LogLevel = LogLevel.DEBUG,
        structured_logs: bool = True,
        enable_performance_logging: bool = True
    ) -> None:
        """
        Set up complete logging configuration.
        
        Args:
            console_level: Log level for console output
            file_level: Log level for file output
            structured_logs: Whether to use structured JSON format for files
            enable_performance_logging: Whether to enable performance logging
        """
        with self._lock:
            # Configure root logger
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.DEBUG)
            
            # Clear existing handlers
            root_logger.handlers.clear()
            
            # Setup console handler
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(console_level.value)
            console_handler.setFormatter(HumanReadableFormatter())
            root_logger.addHandler(console_handler)
            
            # Setup main log file with rotation
            main_log_file = self.base_dir / "ai_rebuild.log"
            main_file_handler = logging.handlers.RotatingFileHandler(
                main_log_file,
                maxBytes=self.max_file_size,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            main_file_handler.setLevel(file_level.value)
            
            if structured_logs:
                main_file_handler.setFormatter(StructuredFormatter())
            else:
                main_file_handler.setFormatter(
                    logging.Formatter(
                        '%(asctime)s | %(levelname)8s | %(name)s | %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S'
                    )
                )
            root_logger.addHandler(main_file_handler)
            
            # Setup component-specific loggers
            self._setup_component_loggers(file_level, structured_logs)
            
            # Setup error-specific logging
            self._setup_error_logging(structured_logs)
            
            # Setup performance logging if enabled
            if enable_performance_logging:
                self._setup_performance_logging(structured_logs)
            
            logging.info("Logging configuration completed")
    
    def _setup_component_loggers(
        self,
        file_level: LogLevel,
        structured_logs: bool
    ) -> None:
        """Set up loggers for different system components."""
        
        components = [
            (LoggerName.SYSTEM, "system.log"),
            (LoggerName.AGENTS, "agents.log"),
            (LoggerName.TOOLS, "tools.log"),
            (LoggerName.INTEGRATIONS, "integrations.log"),
            (LoggerName.SECURITY, "security.log"),
            (LoggerName.DATABASE, "database.log"),
            (LoggerName.CONFIG, "config.log"),
            (LoggerName.API, "api.log"),
        ]
        
        for logger_name, log_file in components:
            logger = logging.getLogger(logger_name.value)
            
            # Create rotating file handler for this component
            file_handler = logging.handlers.RotatingFileHandler(
                self.base_dir / log_file,
                maxBytes=self.max_file_size,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(file_level.value)
            
            if structured_logs:
                file_handler.setFormatter(StructuredFormatter())
            else:
                file_handler.setFormatter(
                    logging.Formatter(
                        '%(asctime)s | %(levelname)8s | %(funcName)s:%(lineno)d | %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S'
                    )
                )
            
            logger.addHandler(file_handler)
            logger.propagate = True  # Also send to root logger
            
            self.configured_loggers.append(logger_name.value)
    
    def _setup_error_logging(self, structured_logs: bool) -> None:
        """Set up dedicated error logging."""
        error_logger = logging.getLogger(LoggerName.ERRORS.value)
        
        # Error log file with rotation
        error_file_handler = logging.handlers.RotatingFileHandler(
            self.base_dir / "errors.log",
            maxBytes=self.max_file_size,
            backupCount=self.backup_count,
            encoding='utf-8'
        )
        error_file_handler.setLevel(logging.ERROR)
        
        if structured_logs:
            error_file_handler.setFormatter(StructuredFormatter(include_extra=True))
        else:
            error_file_handler.setFormatter(
                logging.Formatter(
                    '%(asctime)s | %(levelname)8s | %(name)s | %(funcName)s:%(lineno)d\n'
                    'Message: %(message)s\n'
                    '%(exc_info)s\n' + '-' * 80,
                    datefmt='%Y-%m-%d %H:%M:%S'
                )
            )
        
        error_logger.addHandler(error_file_handler)
        error_logger.propagate = True
        
        self.configured_loggers.append(LoggerName.ERRORS.value)
    
    def _setup_performance_logging(self, structured_logs: bool) -> None:
        """Set up performance logging."""
        perf_logger = logging.getLogger(LoggerName.PERFORMANCE.value)
        
        # Performance log file
        perf_file_handler = logging.handlers.RotatingFileHandler(
            self.base_dir / "performance.log",
            maxBytes=self.max_file_size,
            backupCount=self.backup_count,
            encoding='utf-8'
        )
        perf_file_handler.setLevel(logging.INFO)
        
        if structured_logs:
            perf_file_handler.setFormatter(StructuredFormatter(include_extra=True))
        else:
            perf_file_handler.setFormatter(
                logging.Formatter(
                    '%(asctime)s | PERF | %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                )
            )
        
        perf_logger.addHandler(perf_file_handler)
        perf_logger.propagate = False  # Don't send to root logger
        
        self.configured_loggers.append(LoggerName.PERFORMANCE.value)
    
    def set_logger_level(self, logger_name: str, level: LogLevel) -> None:
        """Set log level for a specific logger."""
        with self._lock:
            logger = logging.getLogger(logger_name)
            logger.setLevel(level.value)
            self.log_levels[logger_name] = level
            logging.info(f"Set logger '{logger_name}' level to {level.name}")
    
    def get_logger(self, name: str) -> logging.Logger:
        """Get a configured logger instance."""
        return logging.getLogger(name)
    
    def log_system_info(self) -> None:
        """Log system information and configuration."""
        system_logger = self.get_logger(LoggerName.SYSTEM.value)
        
        system_info = {
            'python_version': sys.version,
            'platform': sys.platform,
            'log_directory': str(self.base_dir),
            'configured_loggers': self.configured_loggers,
            'log_levels': {name: level.name for name, level in self.log_levels.items()},
            'max_file_size_mb': self.max_file_size / (1024 * 1024),
            'backup_count': self.backup_count
        }
        
        system_logger.info("System information", extra={'system_info': system_info})


class PerformanceLogger:
    """
    Utility class for performance logging.
    """
    
    def __init__(self, logger_name: str = LoggerName.PERFORMANCE.value):
        self.logger = logging.getLogger(logger_name)
    
    def log_duration(
        self,
        operation: str,
        duration_ms: float,
        **extra_fields
    ) -> None:
        """Log operation duration."""
        self.logger.info(
            f"Operation '{operation}' completed in {duration_ms:.2f}ms",
            extra={
                'operation': operation,
                'duration_ms': duration_ms,
                **extra_fields
            }
        )
    
    def log_resource_usage(
        self,
        resource_type: str,
        usage: float,
        limit: Optional[float] = None,
        **extra_fields
    ) -> None:
        """Log resource usage."""
        message = f"Resource usage - {resource_type}: {usage}"
        if limit:
            percentage = (usage / limit) * 100
            message += f" / {limit} ({percentage:.1f}%)"
        
        self.logger.info(
            message,
            extra={
                'resource_type': resource_type,
                'usage': usage,
                'limit': limit,
                'usage_percentage': (usage / limit) * 100 if limit else None,
                **extra_fields
            }
        )


# Global instances
_logging_config = LoggingConfig()
_performance_logger = PerformanceLogger()


def setup_logging(
    console_level: LogLevel = LogLevel.INFO,
    file_level: LogLevel = LogLevel.DEBUG,
    structured_logs: bool = True,
    log_dir: Optional[str] = None
) -> None:
    """
    Setup global logging configuration.
    
    Args:
        console_level: Log level for console output
        file_level: Log level for file output
        structured_logs: Whether to use structured JSON format
        log_dir: Custom log directory path
    """
    global _logging_config
    
    if log_dir:
        _logging_config = LoggingConfig(log_dir)
    
    _logging_config.setup_logging(
        console_level=console_level,
        file_level=file_level,
        structured_logs=structured_logs
    )
    
    # Log system information
    _logging_config.log_system_info()


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance."""
    return _logging_config.get_logger(name)


def get_component_logger(component: LoggerName) -> logging.Logger:
    """Get a logger for a specific system component."""
    return get_logger(component.value)


def set_logger_level(logger_name: str, level: LogLevel) -> None:
    """Set log level for a specific logger."""
    _logging_config.set_logger_level(logger_name, level)


def log_performance(operation: str, duration_ms: float, **extra_fields) -> None:
    """Log performance metrics."""
    _performance_logger.log_duration(operation, duration_ms, **extra_fields)


def log_resource_usage(
    resource_type: str,
    usage: float,
    limit: Optional[float] = None,
    **extra_fields
) -> None:
    """Log resource usage metrics."""
    _performance_logger.log_resource_usage(
        resource_type, usage, limit, **extra_fields
    )


class LogContext:
    """
    Context manager for adding structured context to logs.
    """
    
    def __init__(self, logger: logging.Logger, **context):
        self.logger = logger
        self.context = context
        self.old_factory = None
    
    def __enter__(self):
        self.old_factory = logging.getLogRecordFactory()
        
        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in self.context.items():
                setattr(record, key, value)
            return record
        
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self.old_factory)