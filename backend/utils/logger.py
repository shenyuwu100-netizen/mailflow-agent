"""
Structured Logger

Provides structured logging with context, log levels, and rotation.
Supports JSON formatting for easy parsing and analysis.
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from logging.handlers import RotatingFileHandler


class StructuredLogger:
    """Structured logger with context and JSON formatting."""

    def __init__(
        self,
        name: str,
        log_file: Optional[str] = None,
        level: int = logging.INFO,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5
    ):
        """
        Initialize StructuredLogger.

        Args:
            name: Logger name
            log_file: Path to log file (optional)
            level: Logging level
            max_bytes: Max log file size before rotation
            backup_count: Number of backup files to keep
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False

        # Clear existing handlers
        self.logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # File handler with rotation
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(level)
            file_formatter = JsonFormatter()
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

    def _log_with_context(
        self,
        level: int,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        exc_info: bool = False
    ):
        """Log message with context."""
        extra = {'context': context or {}}
        self.logger.log(level, message, extra=extra, exc_info=exc_info)

    def debug(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Log debug message."""
        self._log_with_context(logging.DEBUG, message, context)

    def info(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Log info message."""
        self._log_with_context(logging.INFO, message, context)

    def warning(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Log warning message."""
        self._log_with_context(logging.WARNING, message, context)

    def error(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        exc_info: bool = False
    ):
        """Log error message."""
        self._log_with_context(logging.ERROR, message, context, exc_info)

    def critical(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        exc_info: bool = False
    ):
        """Log critical message."""
        self._log_with_context(logging.CRITICAL, message, context, exc_info)


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }

        # Add context if available
        if hasattr(record, 'context'):
            log_data['context'] = record.context

        # Add exception info if available
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


# Global logger instances
_loggers: Dict[str, StructuredLogger] = {}


def get_logger(
    name: str,
    log_file: Optional[str] = None,
    level: int = logging.INFO
) -> StructuredLogger:
    """
    Get or create a structured logger.

    Args:
        name: Logger name
        log_file: Path to log file (optional)
        level: Logging level

    Returns:
        StructuredLogger instance
    """
    if name not in _loggers:
        _loggers[name] = StructuredLogger(name, log_file, level)
    return _loggers[name]


# Default application logger
def get_app_logger() -> StructuredLogger:
    """Get default application logger."""
    backend_dir = Path(__file__).parent.parent
    log_file = backend_dir / "logs" / "app.log"
    return get_logger('app', str(log_file), logging.INFO)
