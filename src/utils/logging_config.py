import os
import logging
from logging.handlers import RotatingFileHandler
from src.config.settings import settings

def setup_logging():
    """Configure logging for the application."""
    # Create logs directory if it doesn't exist
    os.makedirs(settings.LOG_DIR, exist_ok=True)

    # Create formatters
    console_formatter = logging.Formatter('%(levelname)s - %(message)s')
    file_formatter = logging.Formatter(settings.LOG_FORMAT)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    # Console handler (less verbose)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(settings.CONSOLE_LOG_LEVEL)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handlers (more verbose)
    # Main debug log file
    debug_handler = RotatingFileHandler(
        os.path.join(settings.LOG_DIR, 'debug.log'),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    debug_handler.setLevel(settings.FILE_LOG_LEVEL)
    debug_handler.setFormatter(file_formatter)
    root_logger.addHandler(debug_handler)

    # Error log file
    error_handler = RotatingFileHandler(
        os.path.join(settings.LOG_DIR, 'error.log'),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    root_logger.addHandler(error_handler)

    # Set specific log levels for noisy modules
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING) 