"""Logging utility for the AI agent system."""

import logging
import os
from pathlib import Path


def setup_logging():
    """Configure logging for the application."""
    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Configure root logger with rotating file handler
    import logging.handlers
    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / 'system.log', 
        maxBytes=10*1024*1024,  # 10MB
        backupCount=3
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            file_handler,
            logging.StreamHandler()  # Also log to console
        ]
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.
    
    Args:
        name: Name for the logger (e.g., 'telegram.voice_transcription')
        
    Returns:
        Configured logger instance
    """
    # Ensure logging is set up
    if not logging.getLogger().handlers:
        setup_logging()
    
    return logging.getLogger(name)


# Set up logging when module is imported
setup_logging()

# Legacy compatibility - keep the old logger instance
logger = logging.getLogger(__name__)
